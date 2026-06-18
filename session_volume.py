#!/usr/bin/env python3
"""
session_volume.py -- Session-window volume comparison for VLM Commodities.

Measures options volume in two windows:
  Overnight : 21:00 (prev calendar day) -> 07:00 ET
  Day       : 07:00 -> 14:20 ET  (cumulative@14:20 - cumulative@07:00)

Data source: read-only from Options_flow_analyzer/data/<date>/ct_options_tape.csv
Output:
  data/<date>/session_volume.txt   (human report)
  data/<date>/session_volume.json  (machine-readable)
  data/history/session_volume_history.csv  (permanent, append-only, idempotent)

Usage:
  python session_volume.py                          # both windows, latest session
  python session_volume.py --date 2026-06-17
  python session_volume.py --window overnight
  python session_volume.py --window day
  python session_volume.py --window both --no-write
  python session_volume.py --backfill               # rebuild history from any tapes <=10d
  python session_volume.py --commodity CT --date 2026-06-16 --no-write
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Optional

import config
from contract_resolver import (
    ContractInfo,
    build_capture_universe,
    ice_to_generic,
    futures_prefix_for,
    parse_ice_code,
    CT_EXCLUDED_PREFIXES,
)

# ---------------------------------------------------------------------------
# TAPE READING
# ---------------------------------------------------------------------------

def _float(x) -> float:
    try:
        return float(x) if x not in (None, '') else 0.0
    except (TypeError, ValueError):
        return 0.0


def _tape_snapshots(tape_path: str) -> list[str]:
    """Return sorted list of unique timestamps present in the tape."""
    if not os.path.isfile(tape_path):
        raise FileNotFoundError(
            f'Options tape not found: {tape_path}\n'
            f'  (looked for: {os.path.abspath(tape_path)})'
        )
    seen = set()
    line_no = 0
    with open(tape_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            line_no += 1
            ts = row.get('timestamp', '')
            if ts:
                seen.add(ts)
    return sorted(seen)


def _last_snapshot_at_or_before(snapshots: list[str], cutoff_ts: str) -> Optional[str]:
    """Last snapshot timestamp <= cutoff_ts, or None."""
    result = None
    for ts in snapshots:
        if ts <= cutoff_ts:
            result = ts
    return result


def _rows_at_snapshot(tape_path: str, target_ts: str) -> list[dict]:
    """Read all rows matching target_ts. Tolerates a partial/corrupt last row."""
    rows = []
    line_no = 0
    with open(tape_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            line_no += 1
            try:
                if row.get('timestamp') == target_ts:
                    rows.append(row)
            except Exception as exc:
                # Tolerate single malformed row (live tape may have incomplete last write)
                import sys as _sys
                print(
                    f'WARNING: skipping malformed row at line {line_no} in '
                    f'{tape_path}: {exc}',
                    file=_sys.stderr,
                )
    return rows


def _window_cutoff(session_date: str, hh: int, mm: int,
                   prev_day: bool = False, inclusive: bool = True) -> str:
    """Build 'YYYY-MM-DD HH:MM:SS' cutoff for a window boundary.

    inclusive=True  -> ':59' -- the snapshot AT or just after HH:MM is included.
                      Use for day-window start (07:00:19 IN) and end (14:20:07 IN).
    inclusive=False -> ':00' -- only snapshots strictly before HH:MM are included.
                      Use for overnight end: the 07:00:19 snapshot must be EXCLUDED
                      so the overnight window reads the last pre-07:00 snapshot.
    """
    d = date.fromisoformat(session_date)
    if prev_day:
        d = d - timedelta(days=1)
    secs = '59' if inclusive else '00'
    return f'{d.isoformat()} {hh:02d}:{mm:02d}:{secs}'


# ---------------------------------------------------------------------------
# CONTRACT FILTERING -- apply 8-slot universe and serial mapping
# ---------------------------------------------------------------------------

def _map_contract(ice_code: str, as_of: str,
                  universe: dict) -> Optional[ContractInfo]:
    """
    Map a raw options-tape contract code to a ContractInfo in the 8-slot universe.

    Steps:
      1. Hard-exclude October (CTV) and August (CTQ).
      2. If it's a serial option, roll it to the parent futures prefix.
      3. Resolve the resulting futures ICE code to a generic slot.
      4. Return None if not in the universe (drops position 3+, etc.).
    """
    body = ice_code.strip().upper()
    three = body[:3]

    # Step 1: hard exclude
    if three in CT_EXCLUDED_PREFIXES:
        return None

    # Step 2: serial roll -- get the underlying futures prefix (e.g. CTZ)
    parent_prefix = futures_prefix_for(body)
    if parent_prefix is None:
        return None

    # Reconstruct the futures ICE code using the parent prefix + original year digit
    # e.g. CTU6 (serial) -> parent CTZ -> CTZ6
    year_digit = body[-1]
    futures_ice = f'{parent_prefix}{year_digit}'

    # Step 3: resolve to generic using as_of date
    info = ice_to_generic(futures_ice, as_of)
    return info   # None if outside universe (pos 3+, excluded month, etc.)


# ---------------------------------------------------------------------------
# VOLUME EXTRACTION PER WINDOW
# ---------------------------------------------------------------------------

def _extract_cumulative(tape_path: str, cutoff_ts: str,
                        as_of: str, universe: dict) -> Optional[dict]:
    """
    Extract cumulative call/put volume per generic slot at the last snapshot
    <= cutoff_ts.

    Returns None if no snapshot exists at or before cutoff_ts.
    Returns dict: {generic_code: {'call': float, 'put': float, 'ice_code': str}}
    """
    snapshots = _tape_snapshots(tape_path)
    snap_ts = _last_snapshot_at_or_before(snapshots, cutoff_ts)
    if snap_ts is None:
        return None

    rows = _rows_at_snapshot(tape_path, snap_ts)

    # Accumulate per generic slot -- sum across all strikes at that snapshot
    per_generic: dict[str, dict] = {}
    for row in rows:
        raw_contract = row.get('contract', '')
        info = _map_contract(raw_contract, as_of, universe)
        if info is None:
            continue
        slot = per_generic.setdefault(info.generic_code, {
            'call': 0.0, 'put': 0.0,
            'ice_code': info.ice_code,
            'delivery_year': info.delivery_year,
            'month_code': info.month_code,
            'month_name': info.month_name,
        })
        slot['call'] += _float(row.get('call_vol'))
        slot['put']  += _float(row.get('put_vol'))

    return {'snapshot_ts': snap_ts, 'per_generic': per_generic}


def extract_overnight(tape_path: str, session_date: str,
                      universe: dict) -> Optional[dict]:
    """Volume cumulative at the last snapshot strictly before 07:00 on session_date.

    inclusive=False so the 07:00:19 snapshot (first of the day session) is excluded.
    The overnight window reads the last pre-07:00 snapshot only.
    """
    cutoff = _window_cutoff(session_date,
                            *config.OVERNIGHT_END_HH_MM,
                            prev_day=False, inclusive=False)
    return _extract_cumulative(tape_path, cutoff, session_date, universe)


def extract_day(tape_path: str, session_date: str,
                universe: dict) -> Optional[dict]:
    """
    Day volume = cumulative@14:20 - cumulative@07:00.
    Returns None if either snapshot is missing or they are the same snapshot.

    inclusive=True (default) so 07:00:19 is the day-start and 14:20:07 is the day-end.
    """
    cutoff_end   = _window_cutoff(session_date, *config.DAY_END_HH_MM,   inclusive=True)
    cutoff_start = _window_cutoff(session_date, *config.DAY_START_HH_MM, inclusive=True)

    snapshots = _tape_snapshots(tape_path)
    ts_end   = _last_snapshot_at_or_before(snapshots, cutoff_end)
    ts_start = _last_snapshot_at_or_before(snapshots, cutoff_start)

    if ts_end is None or ts_start is None:
        return None
    # Must have distinct snapshots -- if they are the same the day hasn't run yet
    if ts_end == ts_start:
        return None

    rows_end   = _rows_at_snapshot(tape_path, ts_end)
    rows_start = _rows_at_snapshot(tape_path, ts_start)

    def _accum(rows):
        d = {}
        for row in rows:
            raw_contract = row.get('contract', '')
            info = _map_contract(raw_contract, session_date, universe)
            if info is None:
                continue
            slot = d.setdefault(info.generic_code, {
                'call': 0.0, 'put': 0.0,
                'ice_code': info.ice_code,
                'delivery_year': info.delivery_year,
                'month_code': info.month_code,
                'month_name': info.month_name,
            })
            slot['call'] += _float(row.get('call_vol'))
            slot['put']  += _float(row.get('put_vol'))
        return d

    cum_end   = _accum(rows_end)
    cum_start = _accum(rows_start)

    # Subtract: day vol = end_cumulative - start_cumulative per generic slot
    per_generic = {}
    all_keys = set(cum_end) | set(cum_start)
    for key in all_keys:
        end_slot   = cum_end.get(key, {})
        start_slot = cum_start.get(key, {})
        # Use end_slot metadata (it's the richer one)
        meta = end_slot if end_slot else start_slot
        call_day = max(0.0, _float(end_slot.get('call', 0)) - _float(start_slot.get('call', 0)))
        put_day  = max(0.0, _float(end_slot.get('put',  0)) - _float(start_slot.get('put',  0)))
        per_generic[key] = {
            'call': call_day,
            'put':  put_day,
            'ice_code':      meta.get('ice_code', ''),
            'delivery_year': meta.get('delivery_year', 0),
            'month_code':    meta.get('month_code', ''),
            'month_name':    meta.get('month_name', ''),
        }

    return {
        'snapshot_ts_end':   ts_end,
        'snapshot_ts_start': ts_start,
        'per_generic':       per_generic,
    }


# ---------------------------------------------------------------------------
# SUMMARISE A WINDOW RESULT
# ---------------------------------------------------------------------------

def _summarise(window_data: dict) -> dict:
    """Flatten per_generic into totals + call/put split."""
    per = window_data.get('per_generic', {})
    total_call = sum(s['call'] for s in per.values())
    total_put  = sum(s['put']  for s in per.values())
    total      = total_call + total_put
    return {
        'total': total,
        'call':  total_call,
        'put':   total_put,
        'pc_ratio': (total_put / total_call) if total_call else None,
        'per_contract': {
            g: {
                'ice_code':      s['ice_code'],
                'delivery_year': s['delivery_year'],
                'month_code':    s['month_code'],
                'month_name':    s['month_name'],
                'call': s['call'],
                'put':  s['put'],
                'total': s['call'] + s['put'],
            }
            for g, s in per.items()
        },
    }


# ---------------------------------------------------------------------------
# RVOL COMPUTATION -- reads permanent history
# ---------------------------------------------------------------------------

def _load_history(commodity: str) -> list[dict]:
    """Load session_volume_history.csv. Returns [] if file doesn't exist."""
    path = config.HISTORY_CSV
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            if row.get('commodity', '').upper() == commodity.upper():
                rows.append(row)
    return rows


def _prior_sessions(history: list[dict], before_date: str,
                    window: str) -> list[float]:
    """
    Extract total volume values from history rows strictly before before_date,
    for the given window ('overnight' or 'day'), most-recent first.
    """
    col = f'{window}_total'
    prior = []
    for row in sorted(history, key=lambda r: r['date'], reverse=True):
        if row['date'] >= before_date:
            continue
        val_str = row.get(col, '')
        if val_str == '':
            continue
        try:
            prior.append(float(val_str))
        except ValueError:
            continue
    return prior


def compute_rvol(current_total: float, prior_values: list[float]) -> dict:
    """
    Compute RVOL tiers for (5, 10, 20, 30, 60) sessions.
    Returns {'5': {'avg': x, 'rvol': y, 'n': z}, ...}
    Gracefully degrades: if fewer sessions than tier, stores n/a.
    """
    result = {}
    for tier in config.LOOKBACK_TIERS:
        key = str(tier)
        subset = prior_values[:tier]
        n = len(subset)
        if n == 0:
            result[key] = {'avg': None, 'rvol': None, 'n': 0,
                           'note': f'n/a (have 0 of {tier})'}
        elif n < tier:
            avg = sum(subset) / n
            rvol = current_total / avg if avg else None
            result[key] = {'avg': avg, 'rvol': rvol, 'n': n,
                           'note': f'n/a (have {n} of {tier})'}
        else:
            avg = sum(subset) / tier
            rvol = current_total / avg if avg else None
            result[key] = {'avg': avg, 'rvol': rvol, 'n': tier, 'note': None}
    return result


# ---------------------------------------------------------------------------
# HISTORY WRITE -- idempotent (overwrites existing row for date+commodity)
# ---------------------------------------------------------------------------

_HISTORY_COLS = [
    'date', 'commodity',
    'overnight_total', 'overnight_call', 'overnight_put', 'overnight_pc_ratio',
    'overnight_snapshot_ts',
    'day_total', 'day_call', 'day_put', 'day_pc_ratio',
    'day_snapshot_ts_start', 'day_snapshot_ts_end',
    'generated_at',
]


def _fmt_float(x, decimals=2) -> str:
    if x is None:
        return ''
    return f'{x:.{decimals}f}'


def write_history(commodity: str, session_date: str,
                  overnight_summary: Optional[dict],
                  day_summary: Optional[dict],
                  overnight_data: Optional[dict],
                  day_data: Optional[dict]) -> None:
    """
    Append or overwrite the row for (session_date, commodity) in the permanent
    history CSV. Idempotent: re-running the same date replaces the row.
    """
    path = config.HISTORY_CSV
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing_rows = []
    if os.path.isfile(path):
        with open(path, newline='', encoding='utf-8') as fh:
            existing_rows = list(csv.DictReader(fh))

    # Build the new row
    now = datetime.now().isoformat(timespec='seconds')
    on = overnight_summary or {}
    dv = day_summary or {}
    new_row = {
        'date':                   session_date,
        'commodity':              commodity.upper(),
        'overnight_total':        _fmt_float(on.get('total')),
        'overnight_call':         _fmt_float(on.get('call')),
        'overnight_put':          _fmt_float(on.get('put')),
        'overnight_pc_ratio':     _fmt_float(on.get('pc_ratio'), 4),
        'overnight_snapshot_ts':  overnight_data.get('snapshot_ts', '') if overnight_data else '',
        'day_total':              _fmt_float(dv.get('total')),
        'day_call':               _fmt_float(dv.get('call')),
        'day_put':                _fmt_float(dv.get('put')),
        'day_pc_ratio':           _fmt_float(dv.get('pc_ratio'), 4),
        'day_snapshot_ts_start':  day_data.get('snapshot_ts_start', '') if day_data else '',
        'day_snapshot_ts_end':    day_data.get('snapshot_ts_end', '') if day_data else '',
        'generated_at':           now,
    }

    # Replace existing row for same date+commodity, or append
    key = (session_date, commodity.upper())
    replaced = False
    out_rows = []
    for row in existing_rows:
        if (row.get('date'), row.get('commodity', '').upper()) == key:
            out_rows.append(new_row)
            replaced = True
        else:
            out_rows.append(row)
    if not replaced:
        out_rows.append(new_row)

    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=_HISTORY_COLS)
        w.writeheader()
        w.writerows(out_rows)


# ---------------------------------------------------------------------------
# TEXT REPORT
# ---------------------------------------------------------------------------

def _fmt_n(n) -> str:
    if n is None:
        return 'n/a'
    return f'{int(round(n)):,}'


def _rvol_line(tier_key: str, tier_data: dict) -> str:
    avg  = tier_data['avg']
    rvol = tier_data['rvol']
    n    = tier_data['n']
    note = tier_data['note']
    if avg is None:
        return f'  RVOL-{tier_key:>2}  n/a (0 sessions)'
    flag = ''
    if rvol is not None:
        if rvol >= 2.0:
            flag = '  *** HIGH (>=2x)'
        elif rvol <= 0.5:
            flag = '  *** LOW (<=0.5x)'
    note_str = f'  [{note}]' if note else ''
    return (f'  RVOL-{tier_key:>2}  avg={_fmt_n(avg):>9}  '
            f'rvol={rvol:.2f}x{flag}{note_str}')


def build_report(commodity: str, session_date: str,
                 window: str,
                 overnight_data: Optional[dict],
                 day_data: Optional[dict],
                 overnight_summary: Optional[dict],
                 day_summary: Optional[dict],
                 history: list[dict]) -> str:
    lines = []
    lines.append(f'SESSION VOLUME -- {commodity} -- {session_date}')
    lines.append('=' * 60)

    def _window_block(label: str, summary: Optional[dict],
                      snap_ts: str, prior: list[float]) -> None:
        lines.append(f'\n{label}')
        lines.append('-' * 40)
        if summary is None:
            lines.append('  No data for this window.')
            return
        tot = summary['total']
        lines.append(f'  Total          : {_fmt_n(tot)}')
        lines.append(f'  Calls / Puts   : {_fmt_n(summary["call"])} / {_fmt_n(summary["put"])}'
                     + (f'   (P/C {summary["pc_ratio"]:.2f})' if summary['pc_ratio'] else ''))
        lines.append(f'  Snapshot       : {snap_ts}')

        # Prior session comparison
        if prior:
            prev = prior[0]
            dod = ((tot / prev - 1) * 100) if prev else None
            arrow = 'UP' if (dod or 0) >= 0 else 'DOWN'
            lines.append(f'  vs prev session: {_fmt_n(prev)}'
                         + (f'   {arrow} {dod:+.0f}%' if dod is not None else ''))

        # RVOL tiers
        rvol = compute_rvol(tot, prior)
        lines.append('')
        for tier in config.LOOKBACK_TIERS:
            lines.append(_rvol_line(str(tier), rvol[str(tier)]))

        # Per-contract breakdown
        lines.append('\n  By contract (total | call | put):')
        ranked = sorted(summary['per_contract'].items(),
                        key=lambda kv: -kv[1]['total'])
        for g, s in ranked:
            if s['total'] <= 0:
                continue
            ice = s['ice_code']
            yr  = s['delivery_year']
            lines.append(
                f'    {g:<8} ({ice}/{yr})  '
                f'{_fmt_n(s["total"]):>9}  |  '
                f'{_fmt_n(s["call"]):>8}  |  {_fmt_n(s["put"]):>8}'
            )
        if all(s['total'] <= 0 for _, s in ranked):
            lines.append('    (no volume recorded)')

    if window in ('overnight', 'both'):
        prior_on = _prior_sessions(history, session_date, 'overnight')
        snap_ts  = overnight_data['snapshot_ts'] if overnight_data else ''
        _window_block('OVERNIGHT (prev 21:00 -> 07:00 ET)',
                      overnight_summary, snap_ts, prior_on)

    if window in ('day', 'both'):
        prior_dv = _prior_sessions(history, session_date, 'day')
        snap_ts  = (f"{day_data['snapshot_ts_start']} -> {day_data['snapshot_ts_end']}"
                    if day_data else '')
        _window_block('DAY (07:00 -> 14:20 ET)',
                      day_summary, snap_ts, prior_dv)

    lines.append('')
    lines.append(f'Generated: {datetime.now().isoformat(timespec="seconds")}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# FIND AVAILABLE TAPE SESSIONS
# ---------------------------------------------------------------------------

def _available_sessions(commodity: str) -> list[str]:
    """Sorted list of YYYY-MM-DD dates that have an options tape."""
    out = []
    if not os.path.isdir(config.OPTIONS_FLOW_DATA):
        raise FileNotFoundError(
            f'OPTIONS_FLOW_DATA directory not found: {config.OPTIONS_FLOW_DATA}'
        )
    for name in sorted(os.listdir(config.OPTIONS_FLOW_DATA)):
        if len(name) == 10 and name[4] == '-' and name[7] == '-':
            tape = config.options_tape_path(name, commodity)
            if os.path.isfile(tape):
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# MAIN PROCESSING FUNCTION
# ---------------------------------------------------------------------------

def process_session(commodity: str, session_date: str,
                    window: str, no_write: bool) -> int:
    """
    Process one session. Returns 0 on success, 1 on fatal error.
    Loud fail: any structural error raises with the offending path.
    """
    # Holiday guard
    if session_date in config.CT_CLOSED_DATES:
        print(f'CT closed on {session_date} -- no session', flush=True)
        return 0

    tape_path = config.options_tape_path(session_date, commodity)
    as_of = session_date

    # Resolve the 8-slot universe for this date
    universe = build_capture_universe(as_of)

    overnight_data = overnight_summary = None
    day_data       = day_summary       = None

    if window in ('overnight', 'both'):
        try:
            overnight_data = extract_overnight(tape_path, session_date, universe)
        except FileNotFoundError as exc:
            print(f'FATAL: {exc}', file=sys.stderr, flush=True)
            return 1
        if overnight_data is None:
            print(
                f'WARNING: No overnight snapshot at or before 07:00 for {session_date}.\n'
                f'  Tape: {tape_path}\n'
                f'  (Sessions before 2026-06-15 started at 05:30 -- overnight window unavailable)',
                file=sys.stderr, flush=True,
            )
        else:
            overnight_summary = _summarise(overnight_data)

    if window in ('day', 'both'):
        try:
            day_data = extract_day(tape_path, session_date, universe)
        except FileNotFoundError as exc:
            print(f'FATAL: {exc}', file=sys.stderr, flush=True)
            return 1
        if day_data is None:
            print(
                f'WARNING: No day snapshots covering 07:00-14:20 for {session_date}.\n'
                f'  Tape: {tape_path}',
                file=sys.stderr, flush=True,
            )
        else:
            day_summary = _summarise(day_data)

    # Load history for RVOL and prior-session comparison
    history = _load_history(commodity)

    # Build report
    report = build_report(
        commodity, session_date, window,
        overnight_data, day_data,
        overnight_summary, day_summary,
        history,
    )
    print(report, flush=True)

    # Write outputs
    if not no_write:
        out_dir = config.session_output_dir(session_date)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            print(
                f'FATAL: Cannot create output directory {out_dir}: {exc}',
                file=sys.stderr, flush=True,
            )
            return 1

        txt_path  = config.session_txt_path(session_date)
        json_path = config.session_json_path(session_date)

        try:
            with open(txt_path, 'w', encoding='utf-8') as fh:
                fh.write(report + '\n')
        except OSError as exc:
            print(f'FATAL: Cannot write {txt_path}: {exc}', file=sys.stderr, flush=True)
            return 1

        payload = {
            'date': session_date,
            'commodity': commodity,
            'window': window,
            'overnight': {
                **(overnight_summary or {}),
                'snapshot_ts': overnight_data['snapshot_ts'] if overnight_data else None,
            } if overnight_data else None,
            'day': {
                **(day_summary or {}),
                'snapshot_ts_start': day_data.get('snapshot_ts_start') if day_data else None,
                'snapshot_ts_end':   day_data.get('snapshot_ts_end')   if day_data else None,
            } if day_data else None,
            'generated_at': datetime.now().isoformat(timespec='seconds'),
        }
        try:
            with open(json_path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, indent=2)
        except OSError as exc:
            print(f'FATAL: Cannot write {json_path}: {exc}', file=sys.stderr, flush=True)
            return 1

        try:
            write_history(commodity, session_date,
                          overnight_summary, day_summary,
                          overnight_data, day_data)
        except OSError as exc:
            print(f'FATAL: Cannot write history {config.HISTORY_CSV}: {exc}',
                  file=sys.stderr, flush=True)
            return 1

    return 0


# ---------------------------------------------------------------------------
# BACKFILL -- reconstruct history from any tapes still present
# ---------------------------------------------------------------------------

def backfill(commodity: str, no_write: bool) -> int:
    """Reconstruct history rows from all available tapes (<=10d window in old repo)."""
    sessions = _available_sessions(commodity)
    if not sessions:
        print(f'No {commodity} tape sessions found under {config.OPTIONS_FLOW_DATA}',
              file=sys.stderr, flush=True)
        return 1
    print(f'Backfill: processing {len(sessions)} sessions: {sessions[0]} ... {sessions[-1]}')
    rc = 0
    for d in sessions:
        print(f'  {d} ...', end=' ', flush=True)
        code = process_session(commodity, d, 'both', no_write)
        print('OK' if code == 0 else 'WARN/ERR')
        if code != 0:
            rc = code
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description='VLM Session-Window Volume Comparison -- CT options tape.',
    )
    ap.add_argument('--commodity', default='CT',
                    help='Commodity code (default: CT)')
    ap.add_argument('--date', default=None,
                    help='Session date YYYY-MM-DD (default: latest available)')
    ap.add_argument('--window', choices=['overnight', 'day', 'both'], default='both',
                    help='Which window(s) to compute (default: both)')
    ap.add_argument('--no-write', action='store_true',
                    help='Print report only; do not write files or history')
    ap.add_argument('--backfill', action='store_true',
                    help='Reconstruct history from all available tapes')
    args = ap.parse_args()

    if args.backfill:
        return backfill(args.commodity, args.no_write)

    # Resolve date
    session_date = args.date
    if session_date is None:
        sessions = _available_sessions(args.commodity)
        if not sessions:
            print(
                f'FATAL: No {args.commodity} options tapes found under '
                f'{config.OPTIONS_FLOW_DATA}',
                file=sys.stderr, flush=True,
            )
            return 1
        session_date = sessions[-1]

    return process_session(args.commodity, session_date, args.window, args.no_write)


if __name__ == '__main__':
    sys.exit(main())
