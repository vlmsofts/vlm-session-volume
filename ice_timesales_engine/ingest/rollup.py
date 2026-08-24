"""
rollup.py -- emit compat history rows to this repo's SIDECAR CSVs.

BLAST-RADIUS GATE (build plan section 7): the shared files in
VLM_Session_Volume_Project/data/history are consumed by the existing engine.
This module writes to SEPARATE sidecar files with the IDENTICAL schema
(config.ROLLUP_SESSION_CSV / ROLLUP_BYCONTRACT_CSV, suffix _ICE) and NEVER
touches the shared files. Merging into the shared files requires Lou's
explicit approval.

Schemas (verified against futures_session_volume.py this session):
  session:      date,commodity,night_total,day_total,full_total,night_share,
                night_day_ratio,source,split_source,generated_at
  per-contract: date,commodity,generic_code,ice_code,month_code,month_name,
                delivery_year,position,night,day,full,generated_at

night/day/full are CLEAN tape sums: all-in MINUS cancelled flow (R11 -- a
Delete-tagged print is a trade ICE busted, so it is not flow and never counts).
The rule lives in ingest/classifier.EXCLUDED_FROM_CLEAN and is applied here via
is_excluded(); this module never restates the predicate.

    night + day == full still holds. What changed is that the cancelled lots are
    no longer inside those totals. They are not lost: _window_sums returns them
    per contract as 'excluded' and emit_*_rows report the session total, per the
    P6.6 row-conservation law (a discard site feeds excluded accounting, never a
    silent drop). The sidecar SCHEMA is unchanged -- 'excluded' is reported, not
    written -- because these files are a shared-schema contract.

    Superseded, on the record: this docstring previously read "night/day/full are
    ALL-IN tape sums". That was true and is now wrong. The locked 2026-07-02
    acceptance numbers it referenced are window_sum's, not this module's, and are
    unaffected: window_sum still reports all-in as 'all' alongside 'clean'.

Idempotent upsert keyed by (date, commodity) / (date, commodity, ice_code).
"""

import csv
import os

import config
from commodity_meta import COMMODITY_MONTHS, MONTH_NAME
from contract_resolver import ice_to_generic
from ingest.classifier import is_excluded
from store.db import Db, now_iso

SESSION_COLS = ['date', 'commodity', 'night_total', 'day_total', 'full_total',
                'night_share', 'night_day_ratio', 'source', 'split_source',
                'generated_at']
BYCONTRACT_COLS = ['date', 'commodity', 'generic_code', 'ice_code',
                   'month_code', 'month_name', 'delivery_year', 'position',
                   'night', 'day', 'full', 'generated_at']


def _window_sums(db: Db, commodity: str, session_date: str) -> dict:
    """{ice_code: {'night','day','full','excluded'}} from ticks.

    Two exclusions, different in kind, both deliberate:
      * window   -- the 'other' preset (post-14:20 prints) is outside the
                    session by definition and never enters night/day/full.
      * R11      -- cancelled flow (classifier.EXCLUDED_FROM_CLEAN) is a BUSTED
                    trade, not flow, and never counts. The test comes from
                    classifier.is_excluded(); this module does not restate it.

    'excluded' is the P6.6 accounting half: the cancelled lots dropped from this
    contract's night/day/full, kept counted and attributable rather than
    silently vanishing. It is returned per contract and reported by the callers;
    it is NOT written into the sidecar CSVs, whose schema is fixed and shared."""
    rows = db.q(
        'SELECT ice_code, window_preset, primary_type, SUM(size) FROM ticks'
        ' WHERE commodity=%s AND session_date=%s'
        ' GROUP BY ice_code, window_preset, primary_type',
        (commodity, session_date))
    per = {}
    for ice, win, ptype, s in rows:
        slot = per.setdefault(ice, {'night': 0.0, 'day': 0.0, 'excluded': 0.0})
        if win not in ('night', 'day'):
            continue
        if is_excluded(ptype):
            slot['excluded'] += s
            continue
        slot[win] += s
    for v in per.values():
        v['full'] = v['night'] + v['day']
    return per


def _upsert_csv(path: str, cols: list, key_cols: list, new_rows: list) -> None:
    """Append-only-with-replace: keep existing rows whose key isn't re-emitted."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.isfile(path):
        with open(path, 'r', newline='', encoding='utf-8') as fh:
            existing = list(csv.DictReader(fh))
    new_keys = {tuple(str(r[k]) for k in key_cols) for r in new_rows}
    kept = [r for r in existing
            if tuple(str(r.get(k, '')) for k in key_cols) not in new_keys]
    merged = kept + [{k: ('' if r[k] is None else r[k]) for k in cols} for r in new_rows]
    merged.sort(key=lambda r: tuple(str(r.get(k, '')) for k in key_cols))
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(merged)
    os.replace(tmp, path)


def emit_session_rows(db: Db, commodity: str, session_date: str) -> dict:
    """One session-level compat row (all contracts aggregated).

    Totals are CLEAN (R11). The cancelled lots excluded from them are returned
    under 'excluded_lots' for P6.6 accounting -- part of the return value, not
    of the fixed sidecar schema, so callers and logs can report what was
    dropped without changing a shared file's columns."""
    cmd = commodity.upper()
    per = _window_sums(db, cmd, session_date)
    night = sum(v['night'] for v in per.values())
    day = sum(v['day'] for v in per.values())
    full = night + day
    excluded = sum(v['excluded'] for v in per.values())
    row = {
        'date': session_date, 'commodity': cmd,
        'night_total': night, 'day_total': day, 'full_total': full,
        'night_share': (night / full) if full else '',
        'night_day_ratio': (night / day) if day else '',
        'source': 'ice_blotter_tape', 'split_source': 'ice_blotter',
        'generated_at': now_iso(),
    }
    _upsert_csv(config.ROLLUP_SESSION_CSV, SESSION_COLS,
                ['date', 'commodity'], [row])
    return dict(row, excluded_lots=excluded)


def emit_contract_rows(db: Db, commodity: str, session_date: str) -> list:
    """Per-contract compat rows for EVERY traded ice_code. generic_code is
    populated only for in-universe contracts (pos 1-2, active months); blank
    otherwise -- out-of-universe still trades and is still recorded.

    night/day/full are CLEAN (R11). Each returned row also carries
    'excluded_lots', the cancelled volume dropped from that contract, for P6.6
    accounting. _upsert_csv projects strictly onto BYCONTRACT_COLS, so the extra
    key is reported to callers and never reaches the shared-schema file."""
    cmd = commodity.upper()
    per = _window_sums(db, cmd, session_date)
    rows = []
    for ice, v in sorted(per.items()):
        month_code = ice[-2]
        try:
            info = ice_to_generic(ice, session_date, prefix=cmd,
                                  active_months=COMMODITY_MONTHS.get(cmd))
        except Exception:
            info = None
        rows.append({
            'date': session_date, 'commodity': cmd,
            'generic_code': info.generic_code if info else '',
            'ice_code': ice,
            'month_code': month_code,
            'month_name': MONTH_NAME.get(month_code, ''),
            'delivery_year': info.delivery_year if info else '',
            'position': info.position if info else '',
            'night': v['night'], 'day': v['day'], 'full': v['full'],
            'generated_at': now_iso(),
            'excluded_lots': v['excluded'],
        })
    if rows:
        _upsert_csv(config.ROLLUP_BYCONTRACT_CSV, BYCONTRACT_COLS,
                    ['date', 'commodity', 'ice_code'], rows)
    return rows
