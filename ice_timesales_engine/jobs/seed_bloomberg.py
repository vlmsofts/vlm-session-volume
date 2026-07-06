r"""
seed_bloomberg.py -- ONE-SHOT historical seed of the bar5m archive from
Bloomberg's intraday tick tape (source='bloomberg'; never touches ticks /
minute_agg / any ICE-side table, and never writes outside this repo's DB).

WHY: Bloomberg retains CT intraday ticks ~6.4 months (hard wall measured at
2025-12-22; interval-independent). The ICE blotter capture only runs forward.
This job pulls that window once so the archive starts ~6.4 months deep; the
daily ICE ingest (+ rollup_ice_bar5m) grows it forward. Sources are labeled
and never mixed in queries.

HOW (everything below verified live this session):
  * DATED tickers ('CTZ26 Comdty' two-digit-year form) -- generics stitch by
    TODAY'S mapping, dated contracts are unambiguous across rolls.
  * IntradayTickRequest, TRADE events, includeConditionCodes=True AND
    includeNonPlottableEvents=True -- without the second flag Bloomberg
    suppresses leg/EFS/EFP/block prints and only outrights return.
  * Times arrive UTC -> converted to naive ET (zoneinfo America/New_York).
  * Session date: ET >= 21:00 rolls to the next trading day (weekends +
    config.CLOSED_DATES['CT'] skipped) -- mirrors the blotter convention.
  * conditionCodes -> primary_type via ingest.bbg_map (reconciled to the ICE
    tape at the exact lot on 4 contract-days; residual 'I' -> 'other').
  * Aggregated straight to 5-minute buckets (Lou ruling: 5-min floor) and
    written via replace_bloomberg_day (delete+reinsert per day+contract =
    idempotent, safe to re-run any slice).

Usage (Terminal must be running):
  python -m jobs.seed_bloomberg --commodity CT                  # full seed
  python -m jobs.seed_bloomberg --commodity CT --tickers CTZ26  # one contract
  python -m jobs.seed_bloomberg --commodity CT --start 2026-06-01 --end 2026-07-02
"""

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from store.db import connect

from ingest.bar5m import floor_5m, replace_bloomberg_day
from ingest.bbg_map import map_bbg_conditions
from ingest.normalize import normalize_contract, to_generic

ET = ZoneInfo('America/New_York')
UTC = timezone.utc
HOST, PORT, TIMEOUT_MS = 'localhost', 8194, 60000

# Bloomberg's measured intraday retention wall (2026-07 probe).
DEFAULT_START = '2025-12-22'

# CT dated contracts alive at some point inside the seed window. Two-digit
# year Bloomberg form; includes the months that expired mid-window (H26, K26,
# N26) and thin Oct (V26) so the archive is complete per contract.
DEFAULT_TICKERS = ['CTH26', 'CTK26', 'CTN26', 'CTV26', 'CTZ26',
                   'CTH27', 'CTK27', 'CTN27', 'CTV27', 'CTZ27']

CHUNK_DAYS = 21          # request window per pull -- keeps responses modest


def log(msg):
    print(msg, flush=True)


def bbg_session():
    import blpapi
    o = blpapi.SessionOptions()
    o.setServerHost(HOST); o.setServerPort(PORT)
    s = blpapi.Session(o)
    if not s.start() or not s.openService('//blp/refdata'):
        raise RuntimeError('Bloomberg session failed -- Terminal running?')
    return blpapi, s


def session_date_of(et_dt: datetime, closed: frozenset) -> str:
    """ET tick time -> ICE session date ('YYYY-MM-DD'), blotter convention:
    >=21:00 belongs to the NEXT trading day (skip weekends + holidays)."""
    d = et_dt.date()
    if et_dt.hour >= 21:
        d += timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in closed:
        d += timedelta(days=1)
    return d.isoformat()


def pull_ticks(blpapi, s, ticker: str, start_utc: datetime, end_utc: datetime):
    """One IntradayTickRequest -> [(utc_dt, size, condcodes)]. TRADE only."""
    svc = s.getService('//blp/refdata')
    req = svc.createRequest('IntradayTickRequest')
    req.set('security', f'{ticker} Comdty')
    req.getElement('eventTypes').appendValue('TRADE')
    req.set('includeConditionCodes', True)
    req.set('includeNonPlottableEvents', True)
    req.set('startDateTime', start_utc.replace(tzinfo=None))
    req.set('endDateTime', end_utc.replace(tzinfo=None))
    s.sendRequest(req)
    out = []
    while True:
        ev = s.nextEvent(TIMEOUT_MS)
        for m in ev:
            if m.hasElement('responseError'):
                raise RuntimeError(f'{ticker}: {m.getElement("responseError")}')
            if not m.hasElement('tickData'):
                continue
            td = m.getElement('tickData')
            if not td.hasElement('tickData'):
                continue
            arr = td.getElement('tickData')
            for i in range(arr.numValues()):
                e = arr.getValueAsElement(i)
                t = e.getElementAsDatetime('time')          # UTC naive
                size = e.getElementAsFloat('size') if e.hasElement('size') else 0.0
                cc = (e.getElementAsString('conditionCodes')
                      if e.hasElement('conditionCodes') else '')
                out.append((t, size, cc))
        if ev.eventType() == blpapi.Event.RESPONSE:
            break
    return out


def seed_ticker(blpapi, s, db, commodity: str, ticker: str,
                start: date, end: date, closed: frozenset,
                unknown: Counter) -> dict:
    """Pull + bucket + write one contract. Returns {'days': n, 'lots': x}."""
    ice_code = normalize_contract(ticker)              # 'CTZ26' -> 'CTZ6'
    # per-day accumulators: day -> {(bucket_ts, ptype): [sum, count]}
    days = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    total = 0.0
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        # UTC request window generously covers the ET sessions in the chunk
        start_utc = datetime.combine(cur, datetime.min.time(), UTC)
        end_utc = datetime.combine(chunk_end + timedelta(days=1),
                                   datetime.min.time(), UTC) + timedelta(hours=23)
        ticks = pull_ticks(blpapi, s, ticker, start_utc, end_utc)
        for t_utc, size, cc in ticks:
            et = t_utc.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)
            sd = session_date_of(et, closed)
            if not (start.isoformat() <= sd <= end.isoformat()):
                continue                                # chunk edge overlap
            ptype = map_bbg_conditions(cc)
            if ptype == 'other' and cc.strip():
                unknown[cc] += size
            key = (floor_5m(et.isoformat()), ptype)
            days[sd][key][0] += size
            days[sd][key][1] += 1
            total += size
        cur = chunk_end + timedelta(days=1)

    for sd, buckets in sorted(days.items()):
        generic = to_generic(ice_code, sd, commodity)
        replace_bloomberg_day(db, commodity, sd, ice_code, generic,
                              {k: tuple(v) for k, v in buckets.items()})
    log(f'  {ticker}: {len(days)} session-days, {total:,.0f} lots '
        f'-> bar5m (source=bloomberg, ice_code={ice_code})')
    return {'days': len(days), 'lots': total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--tickers', nargs='*', default=DEFAULT_TICKERS)
    ap.add_argument('--start', default=DEFAULT_START)
    ap.add_argument('--end', default=date.today().isoformat())
    args = ap.parse_args()

    cmd = args.commodity.upper()
    closed = config.CLOSED_DATES.get(cmd, frozenset())
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    blpapi, s = bbg_session()
    db = connect()
    db.init_schema()                                   # additive (IF NOT EXISTS)
    unknown = Counter()
    tot_days = tot_lots = 0
    try:
        for tk in args.tickers:
            try:
                r = seed_ticker(blpapi, s, db, cmd, tk, start, end,
                                closed, unknown)
                tot_days += r['days']; tot_lots += r['lots']
            except RuntimeError as e:
                log(f'  {tk}: SKIPPED ({e})')
    finally:
        s.stop()
        db.close()

    log(f'\nSEED COMPLETE: {tot_days} contract-days, {tot_lots:,.0f} lots.')
    if unknown:
        log('unknown condition codes (mapped to other, by volume):')
        for cc, v in unknown.most_common(10):
            log(f'  {cc!r}: {v:,.0f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
