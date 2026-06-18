#!/usr/bin/env python3
"""
futures_session_volume.py -- Futures session-window volume engine (Part B).

Data model (RTD-daily architecture, 2026-06-17):
  * FORWARD sessions (today, once sidecar exists):
      full  = cum@1420 - cum@open   (direct from RTD sidecar, source='rtd')
      night = cum@0700 - cum@open
      day   = cum@1420 - cum@0700
      All three come straight from the three boundary readings per contract.
      night + day == full exactly (no share arithmetic needed).

  * HISTORICAL sessions (Bloomberg seed, source='bbg_seed'):
      full only (Bloomberg PX_VOLUME, deep history 2005->present).
      night/day not available (daily totals only from Bloomberg).
      Used ONLY for trailing RVOL (5/10/20/30/60) and December-over-years.
      Never used for today's full — that comes from the RTD sidecar.

  * Bloomberg seed / blpapi puller: manual reseed/extend tool only.
    No daily Bloomberg job. The seed stays static until manually refreshed.

Windows (from sidecar boundary cumulatives):
    night = cum@0700 - cum@open ; day = cum@1420 - cum@0700
    full  = cum@1420 - cum@open  (= night + day exactly)

Universe (HARD): DEC/MAR/MAY/JUL x 1st/2nd generic = 8 slots. October & August
EXCLUDED everywhere.

Outputs:
  data/<date>/futures_session_volume.{txt,json}
  data/history/futures_session_volume_history.csv             (session-level, permanent)
  data/history/futures_session_volume_history_by_contract.csv (per-contract, permanent)

Loud failure: structural error -> non-zero exit with the offending absolute path.
Holiday -> clean exit 0, no write. One malformed row tolerated (WARNING + line).

Usage:
  python futures_session_volume.py --seed --no-write     # load deep history (dry run)
  python futures_session_volume.py --seed                # write permanent history
  python futures_session_volume.py --backtest --n 5 --no-write
  python futures_session_volume.py --date 2026-06-17 --no-write
  python futures_session_volume.py --window eod          # today from RTD sidecar
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from typing import Optional

import config
from contract_resolver import build_capture_universe, ice_to_generic


# Authoritative sidecar schema (the cross-repo contract). The producer
# (Options_flow_analyzer/price_tape.py::_write_sidecar) owns this; we ADAPT to
# it. See Options_flow_analyzer/SIDECAR_CONTRACT.md for the source of truth.
# Read-time guard fails loud on column drift (extra columns are tolerated).
_SIDECAR_EXPECTED_COLS = ['timestamp', 'date', 'commodity', 'contract',
                          'boundary', 'volume', 'oi']


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------

def _float(x) -> Optional[float]:
    if x in (None, ''):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _is_excluded_generic(generic: str) -> bool:
    g = generic.strip().upper()
    return g.startswith('CTOCT') or g.startswith('CTAUG')


def _enrich(generic: str, as_of: str, universe: dict) -> Optional[dict]:
    """Symbology key for a generic IN the 8-slot universe on `as_of`, else None.
    Excludes Oct/Aug and positions >= 3 automatically (universe membership)."""
    g = generic.strip().upper()
    if _is_excluded_generic(g):
        return None
    info = universe.get(g)
    if info is None:
        return None
    return {
        'generic_code':  info.generic_code,
        'ice_code':      info.ice_code,
        'month_code':    info.month_code,
        'month_name':    info.month_name,
        'delivery_year': info.delivery_year,
        'position':      info.position,
    }


# ---------------------------------------------------------------------------
# BLOOMBERG SEED  (deep full-session history — authoritative)
# ---------------------------------------------------------------------------

_SEED_CACHE: Optional[dict] = None   # {date: {generic: full_volume}}


def load_seed(path: Optional[str] = None) -> dict:
    """Load the Bloomberg seed CSV into {date: {generic: full_volume}}.

    Only in-universe-by-name generics (the 8 slots) with a numeric volume are
    kept. Empty volume cells (sparse 2nd generics) are skipped, not zeroed.
    Loud-fails with the absolute path if the file is missing.
    """
    global _SEED_CACHE
    if _SEED_CACHE is not None and path is None:
        return _SEED_CACHE
    p = path or config.FUT_SEED_CSV
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f'Bloomberg seed CSV not found: {p}\n  (looked for: {os.path.abspath(p)})'
        )
    by_date: dict[str, dict] = {}
    line_no = 0
    valid_slots = {'CTDEC1', 'CTDEC2', 'CTMAR1', 'CTMAR2',
                   'CTMAY1', 'CTMAY2', 'CTJUL1', 'CTJUL2'}
    with open(p, newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            line_no += 1
            try:
                d = (row.get('date') or '').strip()
                g = (row.get('generic') or '').strip().upper()
                if not d or g not in valid_slots:
                    continue
                v = _float(row.get('volume'))
                if v is None:
                    continue   # sparse cell — skip, never zero-fill
                by_date.setdefault(d, {})[g] = v
            except Exception as exc:
                print(f'WARNING: skipping malformed seed row at line {line_no} '
                      f'in {p}: {exc}', file=sys.stderr)
    if path is None:
        _SEED_CACHE = by_date
    return by_date


def full_for_date(session_date: str, universe: dict,
                  seed: dict) -> Optional[dict]:
    """Authoritative full-session per-generic for ONE date from the seed.
    Returns {'source','per_generic'} or None if the date is absent."""
    day = seed.get(session_date)
    if not day:
        return None
    per_generic = {}
    for g, vol in day.items():
        meta = _enrich(g, session_date, universe)
        if meta is None:
            continue
        per_generic[g] = {**meta, 'full': vol, 'night': None, 'day': None}
    if not per_generic:
        return None
    return {'source': 'bbg_seed', 'per_generic': per_generic}


def full_total_history(commodity: str, before_date: str,
                       seed: dict) -> list:
    """Trailing 8-slot AGGREGATE full volume per prior session, newest first.
    Counts only sessions that actually have data (graceful degrade at 2005)."""
    out = []
    for d in sorted((x for x in seed if x < before_date), reverse=True):
        tot = sum(v for v in seed[d].values() if v is not None)
        out.append(tot)
    return out


def full_contract_history(generic: str, before_date: str, seed: dict) -> list:
    """Trailing per-generic full volume series, newest first."""
    out = []
    for d in sorted((x for x in seed if x < before_date), reverse=True):
        v = seed[d].get(generic)
        if v is not None:
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# SIDECAR  (RTD boundary readings -> night / day / full directly)
# ---------------------------------------------------------------------------

def read_sidecar_direct(sidecar_path: str, session_date: str,
                        universe: dict) -> Optional[dict]:
    """Read the three boundary cumulatives from the RTD sidecar and compute
    night / day / full directly per contract.

        night = cum@0700 - cum@open
        day   = cum@1420 - cum@0700
        full  = cum@1420 - cum@open  (= night + day exactly)

    Returns {'source': 'rtd', 'per_generic': {generic: {...}}} or None if the
    sidecar is missing or lacks all three boundaries for at least one contract.
    Contracts with only partial boundaries are skipped (not zeroed).
    """
    if not os.path.isfile(sidecar_path):
        return None
    by_boundary: dict[str, dict] = {b: {} for b in config.FUT_BOUNDARIES}
    line_no = 0
    with open(sidecar_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        # Fail-loud schema guard: the sidecar is produced by a SEPARATE repo
        # (Options_flow_analyzer/price_tape.py) and linked only by absolute path
        # with no import, so a producer schema change would otherwise corrupt
        # this consumer silently. Missing expected columns => raise. Extra
        # columns are tolerated (additive changes are allowed by the contract).
        fieldnames = reader.fieldnames
        missing = ([c for c in _SIDECAR_EXPECTED_COLS if c not in fieldnames]
                   if fieldnames is not None else list(_SIDECAR_EXPECTED_COLS))
        if missing:
            raise ValueError(
                f'Sidecar schema drift in {os.path.abspath(sidecar_path)}: '
                f'missing expected column(s) {missing}. '
                f'Expected {_SIDECAR_EXPECTED_COLS}; found {fieldnames}.'
            )
        for row in reader:
            line_no += 1
            try:
                b = (row.get('boundary') or '').strip()
                c = (row.get('contract') or '').strip().upper()
                if b not in by_boundary or not c:
                    continue
                v = _float(row.get('volume'))
                if v is not None:
                    by_boundary[b][c] = v
            except Exception as exc:
                print(f'WARNING: skipping malformed sidecar row at line {line_no} '
                      f'in {sidecar_path}: {exc}', file=sys.stderr)

    per_generic = {}
    all_generics = set()
    for b in config.FUT_BOUNDARIES:
        all_generics |= set(by_boundary[b])

    for raw in all_generics:
        # Sidecar 'contract' may be an ICE code (CTZ6, live price_tape) or a
        # generic code (CTDEC1, fixtures). Normalise to the generic universe key.
        if raw in universe:
            g = raw
        else:
            try:
                info = ice_to_generic(raw, session_date)
            except Exception:
                info = None
            g = info.generic_code if info else None
        if g is None or g not in universe:
            continue   # out-of-universe, Oct/Aug (CTV/CTQ), or position >= 3
        v_open = by_boundary['open'].get(raw)
        v_0700 = by_boundary['0700'].get(raw)
        v_1420 = by_boundary['1420'].get(raw)
        if v_open is None or v_0700 is None or v_1420 is None:
            continue   # partial boundaries -- skip
        meta = _enrich(g, session_date, universe)
        if meta is None:
            continue
        night = max(0.0, v_0700 - v_open)
        day   = max(0.0, v_1420 - v_0700)
        full  = night + day
        if full <= 0:
            continue
        per_generic[g] = {**meta, 'night': night, 'day': day, 'full': full}

    if not per_generic:
        return None
    return {'source': 'rtd', 'per_generic': per_generic}


# ---------------------------------------------------------------------------
# RVOL
# ---------------------------------------------------------------------------

def compute_rvol(current_total: Optional[float], prior_values: list) -> dict:
    result = {}
    cur = current_total
    for tier in config.LOOKBACK_TIERS:
        key = str(tier)
        subset = prior_values[:tier]
        n = len(subset)
        if n == 0 or cur is None:
            result[key] = {'avg': None, 'rvol': None, 'n': n,
                           'note': f'n/a (have {n} of {tier})'}
            continue
        avg = sum(subset) / n
        rvol = (cur / avg) if avg else None
        note = None if n >= tier else f'n/a (have {n} of {tier})'
        result[key] = {'avg': avg, 'rvol': rvol, 'n': n, 'note': note}
    return result


# ---------------------------------------------------------------------------
# SUMMARISE + RATIOS
# ---------------------------------------------------------------------------

def _summarise(window_data: Optional[dict]) -> Optional[dict]:
    if window_data is None:
        return None
    per = window_data['per_generic']

    def _tot(field):
        vals = [s[field] for s in per.values() if s.get(field) is not None]
        return sum(vals) if vals else None

    return {
        'source': window_data.get('source'),
        'night': _tot('night'),
        'day':   _tot('day'),
        'full':  _tot('full'),
        'per_contract': {
            g: {
                'ice_code':      s['ice_code'],
                'delivery_year': s['delivery_year'],
                'month_code':    s['month_code'],
                'month_name':    s['month_name'],
                'position':      s['position'],
                'night': s.get('night'),
                'day':   s.get('day'),
                'full':  s.get('full'),
            } for g, s in per.items()
        },
    }


def _ratios(summary: Optional[dict]) -> dict:
    if not summary:
        return {'night_share': None, 'night_day_ratio': None}
    night, day, full = summary.get('night'), summary.get('day'), summary.get('full')
    ns = (night / full) if (night is not None and full) else None
    ndr = (night / day) if (night is not None and day) else None
    return {'night_share': ns, 'night_day_ratio': ndr}


# ---------------------------------------------------------------------------
# HISTORY  (permanent, append-only, idempotent)
# ---------------------------------------------------------------------------

_FUT_HIST_COLS = [
    'date', 'commodity',
    'night_total', 'day_total', 'full_total',
    'night_share', 'night_day_ratio',
    'source', 'split_source', 'generated_at',
]

_FUT_BYCONTRACT_COLS = [
    'date', 'commodity', 'generic_code', 'ice_code',
    'month_code', 'month_name', 'delivery_year', 'position',
    'night', 'day', 'full', 'generated_at',
]


def _fmt(x, dec=2) -> str:
    return '' if x is None else f'{x:.{dec}f}'


def _idempotent_write(path: str, cols: list, key_fields: tuple,
                      new_rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.isfile(path):
        with open(path, newline='', encoding='utf-8') as fh:
            existing = list(csv.DictReader(fh))
    new_keys = {tuple(r[k] for k in key_fields) for r in new_rows}
    kept = [r for r in existing
            if tuple(r.get(k) for k in key_fields) not in new_keys]
    out = kept + new_rows
    out.sort(key=lambda r: (r.get('date', ''), r.get('generic_code', '')))
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(out)


def _build_history_rows(commodity, session_date, summary, ratios, now):
    s = summary or {}
    session_row = {
        'date': session_date, 'commodity': commodity.upper(),
        'night_total': _fmt(s.get('night')),
        'day_total':   _fmt(s.get('day')),
        'full_total':  _fmt(s.get('full')),
        'night_share':     _fmt(ratios.get('night_share'), 4),
        'night_day_ratio': _fmt(ratios.get('night_day_ratio'), 4),
        'source': s.get('source', ''),
        'split_source': s.get('split_source') or '',
        'generated_at': now,
    }
    bycontract = []
    for g, c in (s.get('per_contract') or {}).items():
        bycontract.append({
            'date': session_date, 'commodity': commodity.upper(),
            'generic_code': g, 'ice_code': c['ice_code'],
            'month_code': c['month_code'], 'month_name': c['month_name'],
            'delivery_year': c['delivery_year'], 'position': c['position'],
            'night': _fmt(c.get('night')), 'day': _fmt(c.get('day')),
            'full': _fmt(c.get('full')), 'generated_at': now,
        })
    return session_row, bycontract


def write_history(commodity, session_date, summary, ratios) -> None:
    now = datetime.now().isoformat(timespec='seconds')
    session_row, bycontract = _build_history_rows(
        commodity, session_date, summary, ratios, now)
    _idempotent_write(config.FUT_HISTORY_CSV, _FUT_HIST_COLS,
                      ('date', 'commodity'), [session_row])
    if bycontract:
        _idempotent_write(config.FUT_HISTORY_BY_CONTRACT, _FUT_BYCONTRACT_COLS,
                          ('date', 'commodity', 'generic_code'), bycontract)


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------

def _n(x) -> str:
    return 'n/a' if x is None else f'{int(round(x)):,}'


def _rvol_line(tier_key: str, td: dict) -> str:
    if td['avg'] is None:
        return f'  RVOL-{tier_key:>2}  n/a (have {td["n"]} of {tier_key})'
    flag = ''
    if td['rvol'] is not None:
        if td['rvol'] >= 2.0:
            flag = '  *** HIGH (>=2x)'
        elif td['rvol'] <= 0.5:
            flag = '  *** LOW (<=0.5x)'
    note = f'  [{td["note"]}]' if td['note'] else ''
    rv = f'{td["rvol"]:.2f}x' if td['rvol'] is not None else 'n/a'
    return f'  RVOL-{tier_key:>2}  avg={_n(td["avg"]):>9}  rvol={rv}{flag}{note}'


def build_report(commodity, session_date, summary, ratios,
                 hist_night, hist_day, hist_full,
                 window: str = 'eod') -> str:
    L = []
    _WINDOW_LABELS = {
        'overnight': 'OVERNIGHT (interim, no-write)',
        'eod':       'EOD (RTD sidecar -- same-day final after 14:20 close)',
    }
    window_label = _WINDOW_LABELS.get(window, window.upper())
    L.append(f'FUTURES SESSION VOLUME -- {commodity} -- {session_date} -- {window_label}')
    L.append('=' * 64)
    if summary is None:
        L.append('\n  No data for this session.')
        L.append(f'\nGenerated: {datetime.now().isoformat(timespec="seconds")}')
        return '\n'.join(L)

    src = summary.get('source', '')
    src_label = {
        'rtd':      'RTD sidecar (live boundary readings)',
        'bbg_seed': 'Bloomberg seed (historical full only)',
    }.get(src, src)
    L.append(f'  Source: {src_label}')

    def _block(label, total, prior):
        L.append(f'\n{label}')
        L.append('-' * 44)
        if total is None:
            L.append('  n/a (historical seed -- night/day not available)')
            return
        L.append(f'  Total : {_n(total)}')
        if prior:
            prev = prior[0]
            dod = ((total / prev - 1) * 100) if prev else None
            arrow = 'UP' if (dod or 0) >= 0 else 'DOWN'
            L.append(f'  vs prev session: {_n(prev)}'
                     + (f'   {arrow} {dod:+.0f}%' if dod is not None else ''))
        rv = compute_rvol(total, prior)
        L.append('')
        for t in config.LOOKBACK_TIERS:
            L.append(_rvol_line(str(t), rv[str(t)]))

    _block('NIGHT (open 21:00 -> 07:00 ET)', summary.get('night'), hist_night)
    _block('DAY (07:00 -> 14:20 ET)',        summary.get('day'),   hist_day)
    _block('FULL (open 21:00 -> 14:20 ET)',  summary.get('full'),  hist_full)

    L.append('\nNIGHT vs DAY tilt')
    L.append('-' * 44)
    ns, ndr = ratios.get('night_share'), ratios.get('night_day_ratio')
    L.append(f'  night_share (night/full)   : {ns:.3f}' if ns is not None
             else '  night_share (night/full)   : n/a')
    L.append(f'  night_day_ratio (night/day): {ndr:.3f}' if ndr is not None
             else '  night_day_ratio (night/day): n/a')

    L.append('\nBy contract (full | night | day):')
    ranked = sorted(summary['per_contract'].items(),
                    key=lambda kv: -(kv[1].get('full') or 0))
    any_row = False
    for g, c in ranked:
        if (c.get('full') or 0) <= 0:
            continue
        any_row = True
        L.append(f'  {g:<8} ({c["ice_code"]}/{c["delivery_year"]})  '
                 f'{_n(c.get("full")):>9}  |  {_n(c.get("night")):>8}  |  {_n(c.get("day")):>8}')
    if not any_row:
        L.append('  (no volume recorded)')

    L.append(f'\nGenerated: {datetime.now().isoformat(timespec="seconds")}')
    return '\n'.join(L)


# ---------------------------------------------------------------------------
# TRAILING HISTORY (from this repo's own permanent file)
# ---------------------------------------------------------------------------

def _prior_from_history(col: str, commodity: str, before_date: str) -> list:
    path = config.FUT_HISTORY_CSV
    if not os.path.isfile(path):
        return []
    with open(path, newline='', encoding='utf-8') as fh:
        rows = [r for r in csv.DictReader(fh)
                if (r.get('commodity') or '').upper() == commodity.upper()
                and r.get('date', '') < before_date]
    out = []
    for r in sorted(rows, key=lambda r: r['date'], reverse=True):
        v = r.get(col, '')
        if v != '':
            try:
                out.append(float(v))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------
# PROCESS ONE SESSION
# ---------------------------------------------------------------------------

def process_session(commodity: str, session_date: str, no_write: bool,
                    seed: Optional[dict] = None,
                    window: str = 'eod') -> int:
    if session_date in config.CT_CLOSED_DATES:
        print(f'CT closed on {session_date} -- no session', flush=True)
        return 0

    universe = build_capture_universe(session_date)
    seed = seed if seed is not None else load_seed()

    # Forward session: read RTD sidecar directly for night/day/full.
    # Historical sessions (--seed, --backtest): use Bloomberg seed for full only.
    sidecar_path = config.futures_sidecar_path(session_date, commodity)
    window_data = read_sidecar_direct(sidecar_path, session_date, universe)
    if window_data is not None:
        summary = _summarise(window_data)
        ratios = _ratios(summary)
    else:
        # No sidecar: fall back to Bloomberg seed for full-only (historical).
        full_data = full_for_date(session_date, universe, seed)
        if full_data is not None:
            summary = _summarise(full_data)
            ratios = _ratios(summary)
        else:
            print(f'WARNING: no RTD sidecar and no Bloomberg seed data for '
                  f'{session_date} -- nothing to report.', file=sys.stderr, flush=True)
            summary = ratios = None

    # Trailing RVOL always from the Bloomberg seed (deep history).
    # night/day trailing history accrues forward from this repo's permanent file.
    hist_full  = full_total_history(commodity, session_date, seed)
    hist_night = _prior_from_history('night_total', commodity, session_date)
    hist_day   = _prior_from_history('day_total',   commodity, session_date)

    report = build_report(commodity, session_date, summary, ratios or {},
                          hist_night, hist_day, hist_full, window=window)
    print(report, flush=True)

    if not no_write and summary is not None:
        out_dir = config.session_output_dir(session_date)
        try:
            os.makedirs(out_dir, exist_ok=True)
            with open(config.fut_session_txt_path(session_date), 'w',
                      encoding='utf-8') as fh:
                fh.write(report + '\n')
            payload = {
                'date': session_date, 'commodity': commodity,
                'window': window,
                'summary': summary, 'ratios': ratios,
                'generated_at': datetime.now().isoformat(timespec='seconds'),
            }
            with open(config.fut_session_json_path(session_date), 'w',
                      encoding='utf-8') as fh:
                json.dump(payload, fh, indent=2)
            write_history(commodity, session_date, summary, ratios)
        except OSError as exc:
            print(f'FATAL: write failed under {out_dir} / history: {exc}',
                  file=sys.stderr, flush=True)
            return 1
    return 0


# ---------------------------------------------------------------------------
# SEED INGESTION  (load the deep Bloomberg history into permanent history)
# ---------------------------------------------------------------------------

def seed_history(commodity: str, no_write: bool) -> int:
    """Populate the permanent full-session history (session + by-contract) from
    the Bloomberg seed CSV. full only (night/day are forward-only). Idempotent."""
    seed = load_seed()
    dates = sorted(seed.keys())
    print(f'SEED: {len(dates)} sessions {dates[0]} -> {dates[-1]} from '
          f'{config.FUT_SEED_CSV}')
    if no_write:
        # Show a few resolved rows as proof without writing.
        sample = dates[-3:]
        for d in sample:
            universe = build_capture_universe(d)
            fd = full_for_date(d, universe, seed)
            s = _summarise(fd)
            print(f'  {d}: full_total={_n(s["full"]) if s else "n/a"}  '
                  f'contracts={sorted((s or {}).get("per_contract", {}).keys())}')
        print('SEED (--no-write): nothing written.')
        return 0

    now = datetime.now().isoformat(timespec='seconds')
    session_rows, bycontract_rows = [], []
    for d in dates:
        universe = build_capture_universe(d)
        fd = full_for_date(d, universe, seed)
        if fd is None:
            continue
        summary = _summarise(fd)
        ratios = _ratios(summary)
        sr, bc = _build_history_rows(commodity, d, summary, ratios, now)
        session_rows.append(sr)
        bycontract_rows.extend(bc)
    _idempotent_write(config.FUT_HISTORY_CSV, _FUT_HIST_COLS,
                      ('date', 'commodity'), session_rows)
    _idempotent_write(config.FUT_HISTORY_BY_CONTRACT, _FUT_BYCONTRACT_COLS,
                      ('date', 'commodity', 'generic_code'), bycontract_rows)
    print(f'SEED written: {len(session_rows)} session rows, '
          f'{len(bycontract_rows)} per-contract rows.')
    print(f'  -> {config.FUT_HISTORY_CSV}')
    print(f'  -> {config.FUT_HISTORY_BY_CONTRACT}')
    return 0


# ---------------------------------------------------------------------------
# BACKTEST  (--no-write over the most recent seed sessions)
# ---------------------------------------------------------------------------

def backtest(commodity: str, n: int) -> int:
    seed = load_seed()
    dates = sorted(seed.keys())[-n:]
    if not dates:
        print('No seed sessions found.', file=sys.stderr)
        return 1
    print(f'BACKTEST (--no-write): last {n} sessions for {commodity}\n')
    rc = 0
    for d in dates:
        print('\n' + '#' * 64)
        code = process_session(commodity, d, no_write=True, seed=seed)
        if code != 0:
            rc = code
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description='VLM Futures Session-Volume engine (night/day/full).')
    ap.add_argument('--commodity', default='CT')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today)')
    ap.add_argument('--window', choices=['overnight', 'eod'], default='eod',
                    help='overnight=interim night report, no-write (07:15 job); '
                         'eod=full RTD session report + write (14:40 job, today)')
    ap.add_argument('--no-write', action='store_true')
    ap.add_argument('--seed', action='store_true',
                    help='Load the deep Bloomberg history into permanent history')
    ap.add_argument('--backtest', action='store_true',
                    help='Print reports for the last N sessions, never writes')
    ap.add_argument('--n', type=int, default=5, help='Backtest session count')
    args = ap.parse_args()

    if args.seed:
        return seed_history(args.commodity, args.no_write)
    if args.backtest:
        return backtest(args.commodity, args.n)

    session_date = args.date or datetime.now().strftime('%Y-%m-%d')

    # overnight: interim print only, never writes
    if args.window == 'overnight':
        args.no_write = True

    return process_session(args.commodity, session_date, args.no_write,
                           window=args.window)


if __name__ == '__main__':
    sys.exit(main())
