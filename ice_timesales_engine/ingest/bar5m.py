"""
bar5m.py -- build the permanent 5-minute archive (table bar5m), source-labeled.

Two producers, one table, never mixed:
  * rollup_ice_bar5m(db, commodity, session_date)
        derive source='ice' buckets from minute_agg (the tape truth). Same
        delete+reinsert day-unit idempotency as rebuild_minute_agg. Runs after
        the normal daily ingest; re-runnable any time.
  * replace_bloomberg_day(db, ...)
        write source='bloomberg' buckets produced by jobs/seed_bloomberg.py
        (the one-shot historical seed). Delete+reinsert per
        (commodity, session_date, ice_code) so the seed is re-runnable.

Window note: night/day boundaries (21:00 / 07:00 / 14:20 ET) are all multiples
of 5 minutes, so every bucket lies wholly inside one window -- window_preset is
computed from the bucket start.
"""

from collections import defaultdict
from datetime import date, datetime

from store.db import Db

from .normalize import assign_window


def floor_5m(ts: str) -> str:
    """ISO naive-ET timestamp -> bucket start 'YYYY-MM-DDTHH:MM' (MM%5==0)."""
    return ts[:14] + f'{(int(ts[14:16]) // 5) * 5:02d}'


def bucket_window(bucket_ts: str, session_date: str) -> str:
    """night | day | other for a bucket start."""
    return assign_window(datetime.fromisoformat(bucket_ts),
                         date.fromisoformat(session_date))


def rollup_ice_bar5m(db: Db, commodity: str, session_date: str) -> int:
    """minute_agg -> bar5m rows (source='ice') for one day. Returns rows."""
    cmd = commodity.upper()
    rows = db.q("""
        SELECT ice_code, generic_code, minute_ts, primary_type,
               sum_size, trade_count
        FROM minute_agg WHERE commodity=%s AND session_date=%s
    """, (cmd, session_date))

    agg = defaultdict(lambda: [0.0, 0])
    generic_of = {}
    for ice, generic, minute_ts, ptype, s, n in rows:
        key = (ice, floor_5m(minute_ts), ptype)
        agg[key][0] += s
        agg[key][1] += n
        generic_of[ice] = generic

    db.exec("DELETE FROM bar5m WHERE source='ice' AND commodity=%s AND session_date=%s",
            (cmd, session_date))
    db.execmany("""
        INSERT INTO bar5m (source, commodity, session_date, ice_code,
                           generic_code, bucket_ts, window_preset,
                           primary_type, sum_size, trade_count)
        VALUES ('ice',%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (cmd, session_date, ice, generic_of[ice], bts,
         bucket_window(bts, session_date), ptype, s, n)
        for (ice, bts, ptype), (s, n) in agg.items()
    ])
    db.commit()
    return len(agg)


def replace_bloomberg_day(db: Db, commodity: str, session_date: str,
                          ice_code: str, generic_code, buckets: dict) -> int:
    """Write one (day, contract) of seeded buckets.
    buckets: {(bucket_ts, primary_type): (sum_size, trade_count)}."""
    cmd = commodity.upper()
    db.exec("""
        DELETE FROM bar5m WHERE source='bloomberg' AND commodity=%s
                             AND session_date=%s AND ice_code=%s
    """, (cmd, session_date, ice_code))
    db.execmany("""
        INSERT INTO bar5m (source, commodity, session_date, ice_code,
                           generic_code, bucket_ts, window_preset,
                           primary_type, sum_size, trade_count)
        VALUES ('bloomberg',%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (cmd, session_date, ice_code, generic_code, bts,
         bucket_window(bts, session_date), ptype, s, n)
        for (bts, ptype), (s, n) in buckets.items()
    ])
    db.commit()
    return len(buckets)
