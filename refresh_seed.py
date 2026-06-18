"""
refresh_seed.py -- Daily Bloomberg seed refresh for vlm_session_volume.

Pulls a trailing window (default 15 trading days) from Bloomberg via
cotton_futures_volume_history_blpapi.py --merge, upserts into the existing
cotton_futures_volume_history.csv, then re-seeds the engine's permanent
history so yesterday's INTRADAY ESTIMATE row becomes FINAL.

Schedule: Mon-Fri ~09:00 ET (after Bloomberg final daily volume posts, ~08:00-08:30).
Followed immediately by the engine's --window final job at 09:15.

Usage:
  python refresh_seed.py              # trailing 15 calendar days (default)
  python refresh_seed.py --days 30    # wider trailing window
  python refresh_seed.py --dry-run    # print commands, execute nothing
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths (absolute; no reliance on cwd) ──────────────────────────────────────
_HERE        = Path(__file__).parent.resolve()
PULLER       = _HERE / 'cotton_futures_volume_history_blpapi.py'
SEED_CSV     = _HERE / 'cotton_futures_volume_history.csv'

_REPO        = Path(r'C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\vlm_session_volume')
ENGINE       = _REPO / 'futures_session_volume.py'

PYTHON       = sys.executable   # same interpreter that runs this script

LOG_DIR      = _REPO / 'logs'


def _run(cmd: list, dry_run: bool, label: str) -> int:
    print(f'\n[{label}]')
    print('  ' + ' '.join(str(c) for c in cmd))
    if dry_run:
        print('  (dry-run: skipped)')
        return 0
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f'  ERROR: exit code {result.returncode}', file=sys.stderr)
    return result.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description='Daily Bloomberg seed refresh + engine finalize')
    ap.add_argument('--days', type=int, default=15,
                    help='Trailing calendar days to re-pull from Bloomberg (default 15)')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print commands without executing')
    args = ap.parse_args()

    today     = datetime.now()
    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    start_dt  = (today - timedelta(days=args.days)).strftime('%Y%m%d')
    end_dt    = today.strftime('%Y%m%d')

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f'refresh_seed.py -- {today:%Y-%m-%d %H:%M:%S}')
    print(f'  Trailing window: {start_dt} -> {end_dt}  ({args.days} calendar days)')
    print(f'  Seed CSV: {SEED_CSV}')
    print(f'  Engine:   {ENGINE}')

    # Step 1: pull trailing window from Bloomberg, merge into seed CSV
    rc = _run([
        PYTHON, str(PULLER),
        '--start', start_dt,
        '--end',   end_dt,
        '--output', str(SEED_CSV),
        '--merge',
    ], args.dry_run, 'Step 1: Bloomberg pull --merge')
    if rc != 0:
        print('FATAL: seed refresh failed -- aborting engine finalize.', file=sys.stderr)
        return rc

    # Step 2: re-run engine for yesterday with --window final to overwrite the
    # INTRADAY ESTIMATE history row with Bloomberg settled volume.
    rc = _run([
        PYTHON, str(ENGINE),
        '--commodity', 'CT',
        '--window',    'final',
        '--date',      yesterday,
    ], args.dry_run, f'Step 2: engine --window final for {yesterday}')

    return rc


if __name__ == '__main__':
    sys.exit(main())
