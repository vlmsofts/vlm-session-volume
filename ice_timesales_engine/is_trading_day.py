#!/usr/bin/env python
"""
is_trading_day.py -- trading-calendar gate for the engine's scheduled ingest.

run_daily_ingest_all.bat fires on a fixed Task Scheduler clock with no regard
for the trading calendar. daily_ingest DOES guard holidays, but it tests the
SESSION date it is about to process, not TODAY -- and with --date omitted it
picks the LATEST day-folder on disk. So on a Saturday, or on a Monday holiday,
it would re-process Friday: harmless (the sha256 skip makes it a no-op) but it
is work and log noise on a day with nothing new to ingest.

This is the gate, mirroring the is_trading_day.py in the ICE eod records repo,
which the three ICE capture tasks already call. Deliberately a SEPARATE copy rather than
an import: this repo must not depend on a path in the capture repo, and the
calendar itself is shared through config.CLOSED_DATES, so there is one calendar
and two thin callers -- not two calendars.

Exit codes:
    0  -> today is a trading day  (run the ingest)
    1  -> weekend or holiday      (skip; prints the reason)

Optional arg: an ISO date (YYYY-MM-DD) to test a specific day instead of today.
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402


def is_trading_day(d: date) -> tuple:
    """(bool, reason). Weekends and CT_CLOSED_DATES are non-trading."""
    iso = d.isoformat()
    if d.weekday() >= 5:
        return False, f'{iso} is a {d.strftime("%A")} -- market closed'
    if iso in config.CLOSED_DATES.get('CT', frozenset()):
        return False, f'{iso} is an ICE holiday -- market closed'
    return True, f'{iso} is a trading day'


def main() -> int:
    if len(sys.argv) > 1:
        try:
            d = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
        except ValueError:
            print(f'ERROR: bad date {sys.argv[1]!r} -- expected YYYY-MM-DD',
                  file=sys.stderr)
            return 2
    else:
        d = date.today()
    ok, reason = is_trading_day(d)
    print(reason)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
