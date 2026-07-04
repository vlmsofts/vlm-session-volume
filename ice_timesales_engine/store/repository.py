"""
repository.py -- query helpers over ticks / minute_agg / flags.

Fast path: sum minute_agg buckets (<=1440/day) for any minute-aligned window.
Sub-minute / non-minute-aligned windows fall back to a ticks scan (rare).
"""

from typing import Optional

from store.db import Db


def _contract_filter(contracts: Optional[list]):
    """SQL fragment + params for an optional ice_code/generic filter.
    Accepts ice codes (CTZ6) and generic codes (CTDEC1) mixed."""
    if not contracts:
        return '', []
    ph = ','.join(['%s'] * len(contracts))
    return f' AND (ice_code IN ({ph}) OR generic_code IN ({ph}))', contracts + contracts


def _types_filter(types: Optional[list]):
    if not types:
        return '', []
    ph = ','.join(['%s'] * len(types))
    return f' AND primary_type IN ({ph})', list(types)


def _is_minute_aligned(ts: str) -> bool:
    """'YYYY-MM-DDTHH:MM' or ':SS'=='00' counts as minute-aligned."""
    return len(ts) <= 16 or ts[17:19] in ('', '00')


def window_sum(db: Db, commodity: str, start: str, end: str,
               contracts: Optional[list] = None,
               types: Optional[list] = None) -> dict:
    """Totals for [start, end) -- by_type, by_contract, all, clean.
    start/end are ISO naive ET strings."""
    use_ticks = not (_is_minute_aligned(start) and _is_minute_aligned(end))
    table = 'ticks' if use_ticks else 'minute_agg'
    ts_col = 'exchange_time' if use_ticks else 'minute_ts'
    size_expr = 'size' if use_ticks else 'sum_size'
    cnt_expr = '1' if use_ticks else 'trade_count'

    cf, cp = _contract_filter(contracts)
    tf, tp = _types_filter(types)
    base = (f'FROM {table} WHERE commodity=%s AND {ts_col} >= %s AND {ts_col} < %s'
            + cf + tf)
    params = [commodity.upper(), start[:16] if not use_ticks else start,
              end[:16] if not use_ticks else end] + cp + tp

    by_type = {t: s for t, s, _ in db.q(
        f'SELECT primary_type, SUM({size_expr}), SUM({cnt_expr}) {base} GROUP BY primary_type',
        params)}
    by_contract = {c: s for c, s in db.q(
        f'SELECT ice_code, SUM({size_expr}) {base} GROUP BY ice_code', params)}
    total = sum(by_type.values())
    return {
        'all': total,
        'clean': total - by_type.get('efs_delete', 0.0),
        'by_type': by_type,
        'by_contract': by_contract,
    }


def profile(db: Db, commodity: str, start: str, end: str, bucket_minutes: int,
            contracts: Optional[list] = None,
            types: Optional[list] = None) -> list:
    """Time-profile: [{bucket_ts, sum_size, trade_count}] over [start, end)."""
    cf, cp = _contract_filter(contracts)
    tf, tp = _types_filter(types)
    rows = db.q(
        'SELECT minute_ts, SUM(sum_size), SUM(trade_count) FROM minute_agg'
        ' WHERE commodity=%s AND minute_ts >= %s AND minute_ts < %s'
        + cf + tf + ' GROUP BY minute_ts ORDER BY minute_ts',
        [commodity.upper(), start[:16], end[:16]] + cp + tp)
    if bucket_minutes <= 1:
        return [{'bucket_ts': ts, 'sum_size': s, 'trade_count': n} for ts, s, n in rows]
    # Fold 1-min buckets into N-min buckets in Python (portable).
    out = {}
    for ts, s, n in rows:
        hh, mm = int(ts[11:13]), int(ts[14:16])
        total_min = hh * 60 + mm
        floored = (total_min // bucket_minutes) * bucket_minutes
        key = f'{ts[:11]}{floored // 60:02d}:{floored % 60:02d}'
        cur = out.setdefault(key, [0.0, 0])
        cur[0] += s
        cur[1] += n
    return [{'bucket_ts': k, 'sum_size': v[0], 'trade_count': v[1]}
            for k, v in sorted(out.items())]


def traded_contracts(db: Db, commodity: str, session_date: str) -> list:
    """[{ice_code, generic_code, total}] traded on a session date."""
    rows = db.q("""
        SELECT ice_code, generic_code, SUM(sum_size) FROM minute_agg
        WHERE commodity=%s AND session_date=%s
        GROUP BY ice_code, generic_code ORDER BY 3 DESC
    """, (commodity.upper(), session_date))
    return [{'ice_code': i, 'generic_code': g, 'total': s} for i, g, s in rows]


def reconcile_rows(db: Db, commodity: str, session_date: str) -> list:
    rows = db.q("""
        SELECT ice_code, tape_total, settle_volume, delta, delta_pct, label
        FROM reconcile_flags WHERE commodity=%s AND session_date=%s
        ORDER BY ice_code
    """, (commodity.upper(), session_date))
    return [{'ice_code': i, 'tape_total': t, 'settle_volume': sv,
             'delta': d, 'delta_pct': dp, 'label': lb}
            for i, t, sv, d, dp, lb in rows]


def available_dates(db: Db, commodity: str) -> list:
    return [r[0] for r in db.q(
        'SELECT DISTINCT session_date FROM minute_agg WHERE commodity=%s ORDER BY 1',
        (commodity.upper(),))]


def freshness(db: Db, commodity: str) -> dict:
    rows = db.q("""
        SELECT MAX(session_date), MAX(ingested_at) FROM ingest_log
        WHERE commodity=%s AND status='ok'
    """, (commodity.upper(),))
    latest_date, latest_ingest = rows[0] if rows else (None, None)
    return {'latest_session': latest_date, 'last_ingest_at': latest_ingest,
            'source': 'ice_blotter'}
