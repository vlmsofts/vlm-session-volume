"""
daily_ingest.py -- orchestrator: one commodity-day through the full pipeline.

Order (build plan section 6):
  1 holiday check -> clean exit 0, no work
  2 discover blotters; none -> log zero, exit 0 (NOT a failure -- zero-volume
    months / no-trade days are by design)
  3 per file: sha256 skip -> parse -> normalize -> classify -> upsert -> log
  4 rebuild minute_agg
  5 block supplement + reconcile flags
  6 rollup -> SIDECAR history CSVs (never the shared VLM files)
  7 Cloudflare purge for the day (no-op when unconfigured)

Usage:
  python -m jobs.daily_ingest                     # CT, latest day-folder on disk
  python -m jobs.daily_ingest --commodity CT --date 2026-07-02
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                            # noqa: E402
from api.cache import purge_day                          # noqa: E402
from ingest import discover, reconcile, rollup, spreads  # noqa: E402
from ingest.aggregator import rebuild_minute_agg         # noqa: E402
from ingest.bar5m import rollup_ice_bar5m                # noqa: E402
from ingest.blotter_parser import file_sha256, read_blotter  # noqa: E402
from ingest.loader import IngestMeta, already_ingested, record_ingest, upsert_ticks  # noqa: E402
from ingest.normalize import normalize_contract, normalize_tick  # noqa: E402
from store.db import connect                             # noqa: E402


def ingest_day(db, commodity: str, session_date: str) -> dict:
    """Run the full pipeline for one commodity-day. Returns a summary dict."""
    cmd = commodity.upper()
    summary = {'commodity': cmd, 'session_date': session_date,
               'files': 0, 'rows_read': 0, 'rows_inserted': 0,
               'skipped_files': 0, 'status': 'ok'}

    if session_date in config.CLOSED_DATES.get(cmd, frozenset()):
        summary['status'] = 'holiday'
        print(f'[{cmd} {session_date}] holiday -- no work.')
        return summary

    files = discover.find_blotter_files(cmd, session_date)
    if not files:
        summary['status'] = 'no_blotter'
        print(f'[{cmd} {session_date}] no blotter files -- zero volume day '
              f'(by design), nothing to ingest.')
        return summary

    for path in files:
        fwd = discover.parse_fwd_from_filename(path)
        ice_code = normalize_contract(f'{cmd} {fwd}')
        sha = file_sha256(path)
        if already_ingested(db, cmd, session_date, ice_code, sha):
            summary['skipped_files'] += 1
            print(f'  {path.name}: unchanged (sha256 match) -- skipped.')
            continue
        rows = [normalize_tick(rt, cmd, session_date) for rt in read_blotter(path)]
        inserted = upsert_ticks(db, rows)
        record_ingest(db, IngestMeta(
            commodity=cmd, session_date=session_date, ice_code=ice_code,
            file_name=path.name, file_sha256=sha, rows_read=len(rows),
            rows_inserted=inserted, status='ok' if rows else 'empty'))
        summary['files'] += 1
        summary['rows_read'] += len(rows)
        summary['rows_inserted'] += inserted
        print(f'  {path.name}: read {len(rows)}, inserted {inserted}.')

    n_agg = rebuild_minute_agg(db, cmd, session_date)
    print(f'  minute_agg rebuilt: {n_agg} buckets.')
    n_b5 = rollup_ice_bar5m(db, cmd, session_date)
    print(f'  bar5m archive (source=ice): {n_b5} buckets.')
    spreads.ingest_block_volume(db, cmd, session_date,
                                discover.spreads_path(cmd, session_date))
    n_rec = reconcile.build_reconcile(db, cmd, session_date,
                                      discover.settle_path(cmd, session_date))
    print(f'  reconcile flags: {n_rec} contracts.')
    sess_row = rollup.emit_session_rows(db, cmd, session_date)
    n_by = len(rollup.emit_contract_rows(db, cmd, session_date))
    print(f'  rollup: session night={sess_row["night_total"]:.0f} '
          f'day={sess_row["day_total"]:.0f} full={sess_row["full_total"]:.0f} '
          f'({n_by} contracts) -> sidecar CSVs.')
    purge_day(cmd, session_date)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description='Daily ICE blotter ingest')
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--date', default=None,
                    help='Session date YYYY-MM-DD (default: latest day-folder)')
    args = ap.parse_args()

    cmd = args.commodity.upper()
    session_date = args.date
    if session_date is None:
        days = discover.find_day_folders(cmd)
        if not days:
            print(f'No day folders for {cmd} under {config.ICE_ROOT} -- nothing to do.')
            return 0
        session_date = days[-1]

    os.makedirs(config.LOG_DIR, exist_ok=True)
    db = connect()
    try:
        summary = ingest_day(db, cmd, session_date)
    finally:
        db.close()
    print(f'[{cmd} {session_date}] done: {summary}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
