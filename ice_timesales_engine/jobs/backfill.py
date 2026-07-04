"""
backfill.py -- ingest every existing day-folder for a commodity (idempotent).

Runs the same per-day pipeline as daily_ingest over whatever folders exist on
disk. Re-running is safe: sha256 skip + ON CONFLICT DO NOTHING.

Usage:
  python -m jobs.backfill --commodity CT
  python -m jobs.backfill --commodity CT --from 2026-07-01 --to 2026-07-02
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import discover                  # noqa: E402
from jobs.daily_ingest import ingest_day     # noqa: E402
from store.db import connect                 # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description='Backfill all on-disk ICE blotter days')
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--from', dest='date_from', default=None)
    ap.add_argument('--to', dest='date_to', default=None)
    args = ap.parse_args()

    cmd = args.commodity.upper()
    days = discover.find_day_folders(cmd, args.date_from, args.date_to)
    if not days:
        print(f'No day folders for {cmd} in range -- nothing to do.')
        return 0

    print(f'Backfilling {cmd}: {len(days)} day folder(s): {days[0]} .. {days[-1]}')
    db = connect()
    results = []
    try:
        for d in days:
            print(f'\n=== {cmd} {d} ===')
            results.append(ingest_day(db, cmd, d))
    finally:
        db.close()

    print('\n=== BACKFILL SUMMARY ===')
    for r in results:
        print(f"  {r['session_date']}: {r['status']}  files={r['files']} "
              f"read={r['rows_read']} inserted={r['rows_inserted']} "
              f"skipped={r['skipped_files']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
