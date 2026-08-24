"""
repository.py -- query helpers over ticks / minute_agg / flags.

Fast path: sum minute_agg buckets (<=1440/day) for any minute-aligned window.
Sub-minute / non-minute-aligned windows fall back to a ticks scan (rare).
"""

from typing import Optional

from ingest.aggressor import BUY, SELL, UNSIDED, side_for_conditions
from ingest.classifier import clean_split, excluded_sql
from store.db import Db


def _contract_filter(contracts: Optional[list]):
    """SQL fragment + params for an optional ice_code/generic filter.
    Accepts ice codes (CTZ6) and generic codes (CTDEC1) mixed."""
    if not contracts:
        return '', []
    ph = ','.join(['%s'] * len(contracts))
    return f' AND (ice_code IN ({ph}) OR generic_code IN ({ph}))', contracts + contracts


def _types_filter(types: Optional[list]):
    """SQL fragment + params for the primary_type filter.

    R11, DEFAULT CLEAN / EXPLICIT DIRTY (classifier.EXCLUDED_FROM_CLEAN owns
    the rule; this only applies it):
      * types given    -> honour them EXACTLY, including an explicit request
                          for efs_delete. Asking for cancelled flow by name
                          must still return it, so no exclusion is layered on.
      * types not given -> the caller took the default, and the default must
                          never draw busted lots as flow. Cancelled buckets are
                          excluded here.

    This is why profile()/traded_contracts() are clean by default without each
    growing its own predicate: they both build their WHERE through this helper."""
    if not types:
        return excluded_sql()
    ph = ','.join(['%s'] * len(types))
    return f' AND primary_type IN ({ph})', list(types)


def _types_filter_all(types: Optional[list]):
    """Like _types_filter but WITHOUT the default R11 exclusion.

    For the one caller that must see every bucket in order to REPORT the
    excluded half: window_sum does the clean/excluded split itself, so filtering
    cancelled rows out of its scan would hide the very lots P6.6 requires it to
    account for. Every other caller wants _types_filter."""
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
    """Totals for [start, end) -- all, clean, excluded, by_type, by_contract.
    start/end are ISO naive ET strings. source='bloomberg' (with
    session_date) serves the day from the bar5m seed archive instead of
    the live ICE tables -- see bloomberg_cutoff.

    THIS FUNCTION IS THE RESOLVER for R11 (cancelled prints never count). It
    does not own the rule -- classifier.EXCLUDED_FROM_CLEAN does -- it applies
    it via clean_split() and reports both halves.

    'clean' means: the all-in total MINUS every bucket R11 classes as cancelled
    flow. Today that is exactly efs_delete, so 'clean' currently reads as
    "all-in minus busted prints". Do not read the name as narrower than the
    rule: if EXCLUDED_FROM_CLEAN ever grows, 'clean' grows with it and this
    docstring is the contract, not the old one-term subtraction. The key name
    is deliberately unchanged so existing callers keep working.

    'excluded' / 'excluded_by_type' are the P6.6 accounting half: the discarded
    lots stay counted and attributable, never silently dropped. The invariant
    clean + excluded == all holds for every window, source and filter."""
    cf, cp = _contract_filter(contracts)
    # _all: this function REPORTS the excluded half, so its scan must see the
    # cancelled buckets. clean_split() below does the excluding, not the SQL.
    tf, tp = _types_filter_all(types)

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
        clean, excluded, excluded_by_type = clean_split(by_type)
        return {
            'all': clean + excluded,
            'clean': clean,
            'excluded': excluded,
            'excluded_by_type': excluded_by_type,
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
    clean, excluded, excluded_by_type = clean_split(by_type)
    return {
        'all': clean + excluded,
        'clean': clean,
        'excluded': excluded,
        'excluded_by_type': excluded_by_type,
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


def side_profile(db: Db, commodity: str, start: str, end: str,
                 contracts: Optional[list] = None,
                 session_date: Optional[str] = None,
                 source: Optional[str] = None) -> dict:
    """Aggressor split for ONE session's window: {side: {lots, prints}}.

    Aggressor-tagged OUTRIGHTS only (primary_type='outright'), same base as
    session_render.aggressor_split -- EFS, EFP, block, leg and cancelled are
    all out by construction (they carry no aggressor stamp). Reads minute_agg
    (ICE era) or bar5m (source='bloomberg', seed era) -- never ticks, so this
    stays a query over the pre-aggregated grain like every other profile call.

    The Bloomberg seed writes side='unsided' for every row (no aggressor was
    captured in that rollup) -- so a seed-era date returns 100% unsided here.
    That is an honest absence, not a bug; the caller must show it, never hide
    or backfill it.
    """
    cf, cp = _contract_filter(contracts)
    where = " AND primary_type='outright'" + cf

    if source == 'bloomberg':
        rows = db.q(
            "SELECT side, SUM(sum_size), SUM(trade_count) FROM bar5m"
            " WHERE source='bloomberg' AND commodity=%s AND session_date=%s"
            ' AND bucket_ts >= %s AND bucket_ts < %s' + where
            + ' GROUP BY side',
            [commodity.upper(), session_date, start[:16], end[:16]] + cp)
    else:
        rows = db.q(
            'SELECT side, SUM(sum_size), SUM(trade_count) FROM minute_agg'
            ' WHERE commodity=%s AND minute_ts >= %s AND minute_ts < %s'
            + where + ' GROUP BY side',
            [commodity.upper(), start[:16], end[:16]] + cp)

    by = {s: {'lots': float(l or 0), 'prints': int(n or 0)} for s, l, n in rows}
    for s in ('buy', 'sell', 'unsided'):
        by.setdefault(s, {'lots': 0.0, 'prints': 0})

    base = by['buy']['lots'] + by['sell']['lots']
    out = {
        'base_lots': base,
        'buy': dict(by['buy']), 'sell': dict(by['sell']),
        'unsided': dict(by['unsided']),
        'outright_total': base + by['unsided']['lots'],
    }
    for side in ('buy', 'sell', 'unsided'):
        d = out[side]
        d['pct_of_base'] = (100.0 * d['lots'] / base) if base else None
        d['clip'] = (d['lots'] / d['prints']) if d['prints'] else None
    out['unsided']['pct_of_base'] = None      # never expressed against the base
    # CONSERVATION: buy + sell + unsided == the outright total. Same standard
    # as store/session_render.py's aggressor_split -- a partition that stops
    # summing is a defect that would otherwise ship as a quietly wrong number.
    assert abs(out['buy']['lots'] + out['sell']['lots']
               + out['unsided']['lots'] - out['outright_total']) < 0.0001
    return out


def volume_at_price(db: Db, commodity: str, ice_code: str,
                    date_from: str, date_to: str,
                    interval: float = 0.05,
                    preset: str = None, start_hhmm: str = None,
                    end_hhmm: str = None) -> dict:
    """Aggressor split by PRICE LEVEL, one contract, over [date_from, date_to].

    Reads ticks directly -- minute_agg/bar5m carry no price column, so this
    is a different grain from every other query in this module. BUCKET AND
    AGGREGATE IN SQL: the price bucket and the GROUP BY both happen in the
    query, so only (bucket, conditions_raw) rows cross the wire, never raw
    ticks. conditions_raw is remapped to a side in PYTHON, but through
    ingest.aggressor.side_for_conditions -- the ONE resolver -- never by a
    second SetByBid/SetByAsk->buy/sell mapping written here (that pattern is
    what tests.test_aggressor_side.TestOneRulingOneMechanism guards against).
    Only 'outright' primary_type carries a real side (aggressor base rule,
    same as session_render.aggressor_split / repo.side_profile) so the WHERE
    clause is filtered there directly -- no non-outright conditions_raw ever
    reaches side_for_conditions from this path.

    A window preset/custom range is applied per print via exchange_time, NOT
    via minute-bucket alignment (ticks has no minute_ts) -- night/day/full
    all narrow the same ticks scan.

    VWAP is its OWN exact SQL aggregate (SUM(price*size)/SUM(size) over the
    unbucketed rows) -- deriving it from bucket midpoints would introduce
    bucketing error the order does not ask for.

    CLIP AT THE PRICE LEVEL: MEASURED AND CUT, 2026-08-24
    -------------------------------------------------------
    Clip (lots/prints) is reported per SESSION (buy/sell/unsided below) but
    NOT per price level. Measured on CTZ6, 2026-08-19/20/21, 0.05 bucket, full
    window: 80 of 85 levels carry 10+ prints on both sides. Of those, 20
    diverge clip >=1.5x between sides. In EVERY ONE of those 20, the
    higher-clip side is also the higher-lots side -- zero exceptions. Below
    1.5x, clip-heavy and lots-heavy agree only 57/80 (71%) across all liquid
    levels, and every disagreement sits in that near-parity band (noise, not
    signal). The largest clip readings are not a thin-bucket artifact either:
    the top-20 by value span 25 to 996 prints, median 214, close to the
    all-levels median of 254.

    RULING (Lou, 2026-08-24): clip restates lots on exactly the levels where
    it has anything to say, so it is redundant at this grain and DROPPED from
    the per-level table (out_levels below carries lots only, no clip, no
    prints). Clip is KEPT at session level -- 1.42 vs 1.31 was the original
    observation that started this workstream, and a session-level character
    summary is a different job from a per-level signal. DO NOT RE-ADD a clip
    column to out_levels without re-running this measurement; the finding
    could change at a different interval or a different sample.

    (An earlier check quoting 88.70 on CTZ6 08-21 read 1.69 vs 2.77 -- that
    was the 0.01-tick single-session figure, quoted against the 0.05
    three-session frame that ships. At the shipping grain 88.70 reads 1.39 vs
    1.45 and does not clear the 1.5x threshold. Recorded so nobody re-derives
    that wrong number a second time.)

    Returns {'levels': [{'price', 'buy': {'lots'}, 'sell': {'lots'},
    'unsided': {'lots'}}, ...], 'buy'/'sell'/'unsided': {lots, prints, clip}
    (session totals), 'vwap', 'outright_total'}.
    """
    where = ("commodity = %s AND ice_code = %s "
             "AND session_date >= %s AND session_date <= %s "
             "AND primary_type = 'outright'")
    params = [commodity.upper(), ice_code.upper(), date_from, date_to]

    if preset in ('night', 'day'):
        # ticks.window_preset is stamped per-row at ingest (the same column
        # aggressor_by_window already filters on) -- reuse it rather than
        # re-deriving night/day boundaries from exchange_time a second place.
        where += ' AND window_preset = %s'
        params.append(preset)
    elif start_hhmm and end_hhmm:
        # Custom clock-time window: ticks has no minute_ts to align to via
        # api.windows, so a custom range is expressed directly against
        # exchange_time's HH:MM slice.
        where += (" AND substr(exchange_time, 12, 5) >= %s"
                  ' AND substr(exchange_time, 12, 5) < %s')
        params.append(start_hhmm)
        params.append(end_hhmm)
    # preset == 'full' (or none given): no window narrowing, matches every
    # print in the date range -- 'full' already is the whole in-session span.

    rows = db.q(f"""
        SELECT ROUND(price / %s) * %s AS bucket, conditions_raw,
               SUM(size), COUNT(*)
        FROM ticks WHERE {where}
        GROUP BY bucket, conditions_raw
        ORDER BY bucket
    """, [interval, interval] + params)

    levels = {}
    totals = {BUY: [0.0, 0], SELL: [0.0, 0], UNSIDED: [0.0, 0]}
    for bucket, cond, size, n in rows:
        side = side_for_conditions('outright', cond)
        d = levels.setdefault(bucket, {BUY: [0.0, 0], SELL: [0.0, 0], UNSIDED: [0.0, 0]})
        d[side][0] += float(size or 0)
        d[side][1] += int(n or 0)
        totals[side][0] += float(size or 0)
        totals[side][1] += int(n or 0)

    def _side_dict(pair):
        lots, prints = pair
        return {'lots': lots, 'prints': prints,
                'clip': (lots / prints) if prints else None}

    def _lots_only(pair):
        # Per-level shape: lots only. See the CLIP AT THE PRICE LEVEL note
        # above -- clip/prints are redundant here, dropped after measurement.
        return {'lots': pair[0]}

    out_levels = [
        {'price': price,
         'buy': _lots_only(d[BUY]), 'sell': _lots_only(d[SELL]),
         'unsided': _lots_only(d[UNSIDED])}
        for price, d in sorted(levels.items())
    ]
    outright_total = sum(totals[s][0] for s in (BUY, SELL, UNSIDED))
    # CONSERVATION: summed across every price level, buy + sell + unsided ==
    # the outright total for the same selection. Same standard as
    # session_render.aggressor_split / repo.side_profile.
    assert abs(sum(lv['buy']['lots'] + lv['sell']['lots'] + lv['unsided']['lots']
                  for lv in out_levels) - outright_total) < 0.0001

    vwap_row = db.q(f"""
        SELECT SUM(price * size), SUM(size) FROM ticks WHERE {where}
    """, params)[0]
    vwap_num, vwap_den = vwap_row
    vwap = (float(vwap_num) / float(vwap_den)) if vwap_den else None

    return {
        'levels': out_levels,
        'buy': _side_dict(totals[BUY]), 'sell': _side_dict(totals[SELL]),
        'unsided': _side_dict(totals[UNSIDED]),
        'outright_total': outright_total,
        'vwap': vwap,
        'interval': interval,
    }


def traded_contracts(db: Db, commodity: str, session_date: str,
                     source: Optional[str] = None) -> list:
    """[{ice_code, generic_code, total}] traded on a session date.
    source='bloomberg' reads the seed archive (dates before the cutoff).

    'total' is CLEAN per R11: cancelled flow never counts (the exclusion comes
    from classifier.EXCLUDED_FROM_CLEAN via excluded_sql, not a local predicate).
    This list drives contract pickers and per-contract tables, both of which are
    client-facing, so the default must not carry busted lots."""
    ex, exp = excluded_sql()
    table = 'bar5m' if source == 'bloomberg' else 'minute_agg'
    seed = " AND source='bloomberg'" if source == 'bloomberg' else ''
    rows = db.q(
        f'SELECT ice_code, generic_code, SUM(sum_size) FROM {table}'
        f' WHERE commodity=%s AND session_date=%s' + seed + ex +
        ' GROUP BY ice_code, generic_code ORDER BY 3 DESC',
        [commodity.upper(), session_date] + exp)
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
