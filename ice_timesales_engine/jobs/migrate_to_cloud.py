r"""
migrate_to_cloud.py -- copy the engine's LOCAL SQLite database to the Postgres
pointed at by DATABASE_URL (Supabase). LOU-EXECUTED ONLY (his 2026-07-05
ruling): this job refuses to run unless DATABASE_URL is explicitly set in the
environment -- there is no default, no stored credential, no auto-run path.

What it does (and nothing else):
  1. SOURCE = the local SQLite file (read-only here; never modified).
  2. DEST   = DATABASE_URL. Creates the engine's six tables if absent
     (schema.sql is portable DDL; IF NOT EXISTS -- additive to the project,
     touches no other schema/table in Supabase).
  3. Copies: ticks, minute_agg, bar5m, ingest_log, reconcile_flags,
     block_supplement -- batched inserts, 5,000 rows per batch.
  4. Verifies: per-table source count == dest count, printed as a table.

Safety: if ANY engine table on the destination already has rows, the job
ABORTS unless --replace is passed (--replace deletes rows from the six engine
tables ONLY, then copies fresh). Local SQLite is never written.

Usage (PowerShell, per SUPABASE_RUNBOOK_LOU.md):
  $env:DATABASE_URL = "postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
  python -m jobs.migrate_to_cloud            # first run
  python -m jobs.migrate_to_cloud --replace  # re-run / refresh
"""

import argparse
import os
import sys

import config
from store.db import Db

TABLES = {
    'ticks': ('commodity', 'session_date', 'ice_code', 'generic_code',
              'exchange_time', 'price', 'size', 'primary_type',
              'conditions_raw', 'seq_num', 'window_preset', 'ingested_at'),
    'minute_agg': ('commodity', 'session_date', 'ice_code', 'generic_code',
                   'minute_ts', 'primary_type', 'sum_size', 'trade_count'),
    'bar5m': ('source', 'commodity', 'session_date', 'ice_code',
              'generic_code', 'bucket_ts', 'window_preset', 'primary_type',
              'sum_size', 'trade_count'),
    'ingest_log': ('commodity', 'session_date', 'ice_code', 'file_name',
                   'file_sha256', 'rows_read', 'rows_inserted', 'status',
                   'ingested_at'),
    'reconcile_flags': ('commodity', 'session_date', 'ice_code', 'tape_total',
                        'settle_volume', 'delta', 'delta_pct', 'label',
                        'generated_at'),
    'block_supplement': ('commodity', 'session_date', 'ice_code',
                         'block_volume', 'source', 'on_tape', 'generated_at'),
}
BATCH = 5000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--replace', action='store_true',
                    help='wipe the six ENGINE tables on the destination first')
    args = ap.parse_args()

    url = os.environ.get('DATABASE_URL', '')
    if not (url.startswith('postgres://') or url.startswith('postgresql://')):
        print('DATABASE_URL is not set (or not postgres) in this window.')
        print('This job never runs without it -- see SUPABASE_RUNBOOK_LOU.md')
        print('Steps 1-2, then re-run:  python -m jobs.migrate_to_cloud')
        return 1

    src = Db('')                                  # local SQLite, read-only use
    print(f'source: {src.path}')
    dest = Db(url)
    print('dest  : Supabase Postgres (connected)')
    dest.init_schema()                            # additive IF NOT EXISTS

    # -- preflight: refuse to clobber unless told to ------------------------
    occupied = []
    for t in TABLES:
        (n,), = dest.q(f'SELECT COUNT(*) FROM {t}')
        if n:
            occupied.append((t, n))
    if occupied and not args.replace:
        print('\nABORT: destination engine tables already have rows:')
        for t, n in occupied:
            print(f'  {t}: {n:,}')
        print('Re-run with --replace to wipe THESE SIX TABLES and copy fresh.')
        return 1
    if occupied:
        for t, _ in occupied:
            dest.exec(f'DELETE FROM {t}')
        dest.commit()
        print(f'--replace: cleared {len(occupied)} destination table(s).')

    # -- copy ---------------------------------------------------------------
    print()
    results = []
    for t, cols in TABLES.items():
        col_list = ', '.join(cols)
        ph = ', '.join(['%s'] * len(cols))
        rows = src.q(f'SELECT {col_list} FROM {t}')
        for i in range(0, len(rows), BATCH):
            dest.execmany(f'INSERT INTO {t} ({col_list}) VALUES ({ph})',
                          rows[i:i + BATCH])
        dest.commit()
        (n_dest,), = dest.q(f'SELECT COUNT(*) FROM {t}')
        ok = n_dest == len(rows)
        results.append((t, len(rows), n_dest, ok))
        print(f'  {t:16} {len(rows):>9,} -> {n_dest:>9,}  {"OK" if ok else "** MISMATCH **"}')

    src.close()
    dest.close()
    bad = [r for r in results if not r[3]]
    print(f'\nMIGRATION {"COMPLETE -- all counts verified" if not bad else "FAILED -- mismatches above"}.')
    print('Local SQLite untouched; unset DATABASE_URL to keep running locally.')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
