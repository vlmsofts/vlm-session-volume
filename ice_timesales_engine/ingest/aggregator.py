"""
aggregator.py -- (re)build minute_agg for one (commodity, session_date).

Delete + reinsert (the day is the unit of work) -- safe to re-run any time.
Minute truncation is done in Python (portable across Postgres/SQLite; ISO
strings truncate at char 16: 'YYYY-MM-DDTHH:MM').
"""

from collections import defaultdict

from store.db import Db


def rebuild_minute_agg(db: Db, commodity: str, session_date: str) -> int:
    """Rebuild minute buckets for a day from ticks. Returns rows inserted."""
    cmd = commodity.upper()
    rows = db.q("""
        SELECT ice_code, generic_code, exchange_time, primary_type, size
        FROM ticks WHERE commodity=%s AND session_date=%s
    """, (cmd, session_date))

    agg = defaultdict(lambda: [0.0, 0])          # key -> [sum_size, count]
    generic_of = {}
    for ice_code, generic_code, ts, ptype, size in rows:
        minute_ts = ts[:16]                       # 'YYYY-MM-DDTHH:MM'
        key = (ice_code, minute_ts, ptype)
        agg[key][0] += size
        agg[key][1] += 1
        generic_of[ice_code] = generic_code

    db.exec('DELETE FROM minute_agg WHERE commodity=%s AND session_date=%s',
            (cmd, session_date))
    db.execmany("""
        INSERT INTO minute_agg (commodity, session_date, ice_code, generic_code,
                                minute_ts, primary_type, sum_size, trade_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, [
        (cmd, session_date, ice, generic_of[ice], minute_ts, ptype, s, n)
        for (ice, minute_ts, ptype), (s, n) in agg.items()
    ])
    db.commit()
    return len(agg)
