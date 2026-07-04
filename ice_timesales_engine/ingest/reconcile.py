"""
reconcile.py -- tape total vs futures_settle Volume -> reconcile_flags.

SURFACE, NEVER ENFORCE. The settle file is a cross-check, not ground truth
(verified stale carry-forward rows). The tape running BELOW official volume is
EXPECTED (implied/spread-matched/block fills never print as outright ticks --
07-02 Z26: spreads executed 6,709 legs vs 4,766 leg-ticks printed). Labels:

  expected_gap     settle >= tape and gap within plausible band
  suspect_capture  tape > settle (tape can't exceed all-in official volume)
                   or gap implausibly large (> SUSPECT_GAP_PCT of settle)
  no_settle        settle file missing / contract absent / non-positive value
"""

import csv
import sys
from pathlib import Path

from store.db import Db, now_iso

from .normalize import normalize_contract

# Gap above this share of settle volume is flagged suspect. The verified 07-02
# Z26 gap was 32% of settle -- and even that day is arguably a short capture;
# 50% leaves headroom for heavy-spread days without silencing real failures.
SUSPECT_GAP_PCT = 0.50


def _read_settle_volumes(settle_path: Path, commodity: str) -> dict:
    """{ice_code: settle_volume} from futures_settle_<date>.csv (header
    verified: Date,Contract,...,Volume,OpenInt)."""
    out = {}
    with open(settle_path, 'r', newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            try:
                ice = normalize_contract(row['Contract'])
                vol = float(row['Volume']) if row.get('Volume') not in (None, '') else None
            except (KeyError, TypeError, ValueError) as exc:
                print(f'WARNING: bad settle row {row!r}: {exc}', file=sys.stderr)
                continue
            if vol is not None:
                out[ice] = vol
    return out


def build_reconcile(db: Db, commodity: str, session_date: str,
                    settle_path) -> int:
    cmd = commodity.upper()
    tape = dict(db.q("""
        SELECT ice_code, SUM(size) FROM ticks
        WHERE commodity=%s AND session_date=%s GROUP BY ice_code
    """, (cmd, session_date)))
    settle = {}
    if settle_path is not None and Path(settle_path).is_file():
        settle = _read_settle_volumes(Path(settle_path), cmd)

    db.exec('DELETE FROM reconcile_flags WHERE commodity=%s AND session_date=%s',
            (cmd, session_date))
    params = []
    for ice, tape_total in sorted(tape.items()):
        sv = settle.get(ice)
        if sv is None or sv <= 0:
            delta = delta_pct = None
            label = 'no_settle'
        else:
            delta = sv - tape_total
            delta_pct = delta / sv
            if tape_total > sv or delta_pct > SUSPECT_GAP_PCT:
                label = 'suspect_capture'
            else:
                label = 'expected_gap'
        params.append((cmd, session_date, ice, tape_total, sv, delta,
                       delta_pct, label, now_iso()))
    if params:
        db.execmany("""
            INSERT INTO reconcile_flags (commodity, session_date, ice_code,
                tape_total, settle_volume, delta, delta_pct, label, generated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (commodity, session_date, ice_code) DO UPDATE SET
              tape_total=excluded.tape_total, settle_volume=excluded.settle_volume,
              delta=excluded.delta, delta_pct=excluded.delta_pct,
              label=excluded.label, generated_at=excluded.generated_at
        """, params)
    db.commit()
    return len(params)
