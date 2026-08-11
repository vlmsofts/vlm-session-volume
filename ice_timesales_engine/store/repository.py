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


def bloomberg_cutoff(db: Db, commodity: str) -> Optional[str]:
    """Last session_date covered by the Bloomberg seed (None = no seed).

    Source rule C (Lou, 2026-07-14): one source per era, never mixed --
    session dates AT OR BEFORE this cutoff are served from bar5m
    source='bloomberg' (complete, verified-exact); later dates from the live
    ICE tables. Chosen over 'prefer ice' because the ICE capture of
    2026-05-01 (retention edge) is ~53% partial while the seed is complete."""
    rows = db.q("SELECT MAX(session_date) FROM bar5m"
                " WHERE commodity=%s AND source='bloomberg'",
                (commodity.upper(),))
    return rows[0][0] if rows else None


def window_sum(db: Db, commodity: str, start: str, end: str,
               contracts: Optional[list] = None,
               types: Optional[list] = None,
               session_date: Optional[str] = None,
               source: Optional[str] = None) -> dict:
    """Totals for [start, end) -- by_type, by_contract, all, clean.
    start/end are ISO naive ET strings. source='bloomberg' (with
    session_date) serves the day from the bar5m seed archive instead of
    the live ICE tables -- see bloomberg_cutoff."""
    cf, cp = _contract_filter(contracts)
    tf, tp = _types_filter(types)

    if source == 'bloomberg':
        # Seed grain is 5 min and window presets are 5-min aligned, so
        # bucket_ts range sums are exact. (A non-5-min-aligned custom
        # window is approximated to whole buckets starting in-range.)
        base = ("FROM bar5m WHERE source='bloomberg' AND commodity=%s"
                ' AND session_date=%s AND bucket_ts >= %s AND bucket_ts < %s'
                + cf + tf)
        params = [commodity.upper(), session_date,
                  start[:16], end[:16]] + cp + tp
        by_type = {t: s for t, s, _ in db.q(
            f'SELECT primary_type, SUM(sum_size), SUM(trade_count) {base}'
            ' GROUP BY primary_type', params)}
        by_contract = {c: s for c, s in db.q(
            f'SELECT ice_code, SUM(sum_size) {base} GROUP BY ice_code',
            params)}
        total = sum(by_type.values())
        return {
            'all': total,
            'clean': total - by_type.get('efs_delete', 0.0),
            'by_type': by_type,
            'by_contract': by_contract,
        }

    use_ticks = not (_is_minute_aligned(start) and _is_minute_aligned(end))
    table = 'ticks' if use_ticks else 'minute_agg'
    ts_col = 'exchange_time' if use_ticks else 'minute_ts'
    size_expr = 'size' if use_ticks else 'sum_size'
    cnt_expr = '1' if use_ticks else 'trade_count'

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


def _fold(rows, bucket_minutes: int) -> list:
    """Fold (ts, sum, count) rows into N-min buckets in Python (portable).
    bucket_minutes <= 1 passes rows through at their native grain."""
    if bucket_minutes <= 1:
        return [{'bucket_ts': ts, 'sum_size': s, 'trade_count': n} for ts, s, n in rows]
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


def _collapse(rows, label_ts: str) -> list:
    """Sum ALL rows into a single bucket labeled at label_ts (the window's
    own start) -- unlike _fold, this never floors by clock-time-of-day, so
    it's the only correct way to produce one bar for a window that spans
    midnight (e.g. the night session, 21:00->07:00). Empty rows -> []."""
    if not rows:
        return []
    total_s = sum(r[1] for r in rows)
    total_n = sum(r[2] for r in rows)
    return [{'bucket_ts': label_ts[:16], 'sum_size': total_s, 'trade_count': total_n}]


def profile(db: Db, commodity: str, start: str, end: str, bucket_minutes,
            contracts: Optional[list] = None,
            types: Optional[list] = None,
            session_date: Optional[str] = None,
            source: Optional[str] = None) -> list:
    """Time-profile: [{bucket_ts, sum_size, trade_count}] over [start, end).
    source='bloomberg' (with session_date) reads the bar5m seed archive;
    its native grain is 5 min, so a finer request is served at 5 min.
    bucket_minutes='full' collapses the WHOLE [start, end) window to a single
    row labeled at `start` -- one bar per session, correct even for windows
    that cross midnight (night sessions), unlike a large numeric bucket which
    would still floor-fold on clock-time-of-day and split at 00:00."""
    cf, cp = _contract_filter(contracts)
    tf, tp = _types_filter(types)
    full = (bucket_minutes == 'full')

    if source == 'bloomberg':
        rows = db.q(
            "SELECT bucket_ts, SUM(sum_size), SUM(trade_count) FROM bar5m"
            " WHERE source='bloomberg' AND commodity=%s AND session_date=%s"
            ' AND bucket_ts >= %s AND bucket_ts < %s'
            + cf + tf + ' GROUP BY bucket_ts ORDER BY bucket_ts',
            [commodity.upper(), session_date,
             start[:16], end[:16]] + cp + tp)
        if full:
            return _collapse(rows, start)
        eff = max(bucket_minutes, 5)
        if eff <= 5:
            return [{'bucket_ts': ts, 'sum_size': s, 'trade_count': n}
                    for ts, s, n in rows]
        return _fold(rows, eff)

    rows = db.q(
        'SELECT minute_ts, SUM(sum_size), SUM(trade_count) FROM minute_agg'
        ' WHERE commodity=%s AND minute_ts >= %s AND minute_ts < %s'
        + cf + tf + ' GROUP BY minute_ts ORDER BY minute_ts',
        [commodity.upper(), start[:16], end[:16]] + cp + tp)
    if full:
        return _collapse(rows, start)
    return _fold(rows, bucket_minutes)


def traded_contracts(db: Db, commodity: str, session_date: str,
                     source: Optional[str] = None) -> list:
    """[{ice_code, generic_code, total}] traded on a session date.
    source='bloomberg' reads the seed archive (dates before the cutoff)."""
    if source == 'bloomberg':
        rows = db.q("""
            SELECT ice_code, generic_code, SUM(sum_size) FROM bar5m
            WHERE source='bloomberg' AND commodity=%s AND session_date=%s
            GROUP BY ice_code, generic_code ORDER BY 3 DESC
        """, (commodity.upper(), session_date))
    else:
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
    """All queryable session dates: live ICE days (minute_agg) plus the
    Bloomberg seed era (bar5m). UNION dedupes the overlap days."""
    return [r[0] for r in db.q(
        'SELECT session_date FROM minute_agg WHERE commodity=%s'
        ' UNION'
        ' SELECT session_date FROM bar5m WHERE commodity=%s'
        ' ORDER BY 1',
        (commodity.upper(), commodity.upper()))]


def freshness(db: Db, commodity: str) -> dict:
    rows = db.q("""
        SELECT MAX(session_date), MAX(ingested_at) FROM ingest_log
        WHERE commodity=%s AND status='ok'
    """, (commodity.upper(),))
    latest_date, latest_ingest = rows[0] if rows else (None, None)
    return {'latest_session': latest_date, 'last_ingest_at': latest_ingest,
            'source': 'ice_blotter'}
