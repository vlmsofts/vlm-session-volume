"""
catchup_ingest.py -- second pass: ingest any commodity-day the daily run missed.

Why this exists
---------------
The daily ingest fires on a fixed clock (15:00 and 17:10 ET). The ICE softs
capture fires at 17:00 and loops KC -> SB -> CC sequentially, so on a slow
evening the last commodities in that loop are still being written when 17:10
comes round. On 2026-09-18 CC finished at 17:29 and SB needed a manual rerun
that landed at 20:13; both were skipped by the 17:10 ingest and nothing ran
again, so CC/SB lost the whole session. See MEMORY.md 2026-09-20.

The fix is not to move a clock -- the next late capture just moves past the new
one too. It is to make a later pass ask the only question that matters:

    is there a commodity-day whose blotter files are ON DISK
    but which has NO rows in the database?

That question is answered by `select_catchup_days()` below, which is pure: it
takes a filesystem listing and a set of (commodity, date) keys already in the
DB, and returns what to run. It is deliberately free of I/O so the selection
rule can be tested hermetically.

Non-spinning guarantee
----------------------
A day with NO futures blotter files is never selected, whatever the DB says.
That is what keeps the job from re-running forever on a genuine zero-volume or
holiday-adjacent day (e.g. 2026-07-03, whose day folders hold settle and
settled_surface files but no futures blotters, and which therefore legitimately
has no rows in the database and never will).

Usage:
  python -m jobs.catchup_ingest                  # last 5 trading days, all 4
  python -m jobs.catchup_ingest --days 10
  python -m jobs.catchup_ingest --commodity CC --days 3
  python -m jobs.catchup_ingest --dry-run        # report only, no writes
"""

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                    # noqa: E402
from ingest import discover                      # noqa: E402
from jobs.daily_ingest import ingest_day         # noqa: E402
from store.db import connect                     # noqa: E402

COMMODITIES = ('CT', 'KC', 'SB', 'CC')


def trading_days_back(n: int, today: date = None) -> list[str]:
    """The last n trading days (weekday and not an ICE holiday), oldest first.

    Today is INCLUDED when it is a trading day -- a capture that landed this
    evening is exactly what this job is for.
    """
    d = today or date.today()
    closed = config.CLOSED_DATES.get('CT', frozenset())
    out = []
    while len(out) < n:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in closed:
            out.append(iso)
        d -= timedelta(days=1)
    return sorted(out)


def select_catchup_days(candidates, blotter_counts, db_keys, closed_dates=()):
    """Pure selection rule. No I/O -- see the module docstring.

    candidates     : iterable of (commodity, session_date)
    blotter_counts : {(commodity, session_date): number of futures blotter files}
                     missing key == 0 files
    db_keys        : set of (commodity, session_date) that already have rows
    closed_dates   : iterable of 'YYYY-MM-DD' the market was shut

    Returns the sorted list of (commodity, session_date) to ingest.
    """
    closed = set(closed_dates)
    out = []
    for cmd, day in candidates:
        if day in closed:
            continue
        if blotter_counts.get((cmd, day), 0) <= 0:
            continue                      # nothing captured -> nothing to do
        if (cmd, day) in db_keys:
            continue                      # already ingested
        out.append((cmd, day))
    return sorted(out)


def _db_keys(db, commodities, days) -> set:
    """(commodity, session_date) pairs that already have bar5m rows.

    bar5m rather than ticks: bar5m is the artifact the gateway serves, and it
    is rebuilt delete-and-reinsert per day, so its presence means the whole
    pipeline ran -- not just that some ticks landed.
    """
    if not commodities or not days:
        return set()
    c_ph = ','.join(['%s'] * len(commodities))
    d_ph = ','.join(['%s'] * len(days))
    rows = db.q(
        f'SELECT commodity, session_date FROM bar5m '
        f'WHERE commodity IN ({c_ph}) AND session_date IN ({d_ph}) '
        f'GROUP BY commodity, session_date HAVING COUNT(*) > 0',
        tuple(commodities) + tuple(days))
    return {(r[0], r[1]) for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description='Catch-up ICE blotter ingest')
    ap.add_argument('--days', type=int, default=5,
                    help='How many trailing trading days to check (default 5)')
    ap.add_argument('--commodity', default=None,
                    help='Restrict to one commodity (default: all four)')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be ingested; write nothing')
    args = ap.parse_args()

    commodities = [args.commodity.upper()] if args.commodity else list(COMMODITIES)
    days = trading_days_back(args.days)

    candidates = [(c, d) for c in commodities for d in days]
    counts = {(c, d): len(discover.find_blotter_files(c, d)) for c, d in candidates}

    os.makedirs(config.LOG_DIR, exist_ok=True)
    db = connect()
    try:
        have = _db_keys(db, commodities, days)
        todo = select_catchup_days(candidates, counts, have,
                                   config.CLOSED_DATES.get('CT', frozenset()))
        print(f'[catchup] window {days[0]}..{days[-1]}, commodities '
              f'{",".join(commodities)}: {len(todo)} commodity-day(s) to ingest.')
        if not todo:
            print('[catchup] nothing missing -- all captured days are ingested.')
            return 0
        for cmd, day in todo:
            print(f'[catchup] {cmd} {day}: {counts[(cmd, day)]} blotter file(s) '
                  f'on disk, no bar5m rows -- ingesting.')
            if args.dry_run:
                continue
            summary = ingest_day(db, cmd, day)
            print(f'[catchup] {cmd} {day} done: {summary}')
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
