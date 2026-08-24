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

from .classifier import excluded_sql
from .normalize import normalize_contract

# Gap above this share of settle volume is flagged suspect. The verified 07-02
# Z26 gap was 32% of settle -- and even that day is arguably a short capture;
# 50% leaves headroom for heavy-spread days without silencing real failures.
SUSPECT_GAP_PCT = 0.50


def _read_settle_volumes(settle_path: Path, commodity: str) -> tuple:
    """({ice_code: volume}, vintage) from futures_settle_<date>.csv.

    SAME-DAY WHEN AVAILABLE. Two columns can carry volume:

      CumVolume  the session's own total, from ICE 'Cumulative Volume'. Written
                 by the capture from the 2026-08-24 cutover onward. SAME-DAY.
      Volume     from ICE 'Volume', which returns a flat 0.0 on ICE futures
                 symbols; what actually lands is the PRIOR session's figure.
                 Measured over 36 CT sessions: 165 contract-days match the D-1
                 blotter sum exactly, 5 match same-day.

    Prefer CumVolume; fall back to Volume for the 369 pre-cutover files so
    history stays readable. The returned vintage says which was used, so a
    caller never has to guess whether it is comparing like with like. See
    SAME_DAY_VOLUME_CUTOVER.md in the ICE eod records repo."""
    out = {}
    used_cum = used_legacy = 0
    with open(settle_path, 'r', newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            try:
                ice = normalize_contract(row['Contract'])
                if row.get('CumVolume') not in (None, ''):
                    vol = float(row['CumVolume'])
                    used_cum += 1
                elif row.get('Volume') not in (None, ''):
                    vol = float(row['Volume'])
                    used_legacy += 1
                else:
                    vol = None
            except (KeyError, TypeError, ValueError) as exc:
                print(f'WARNING: bad settle row {row!r}: {exc}', file=sys.stderr)
                continue
            if vol is not None:
                out[ice] = vol
    if used_cum and not used_legacy:
        vintage = 'same_day'
    elif used_legacy and not used_cum:
        vintage = 'prior_session'
    elif used_cum or used_legacy:
        vintage = 'mixed'
    else:
        vintage = 'none'
    return out, vintage


def build_reconcile(db: Db, commodity: str, session_date: str,
                    settle_path) -> int:
    cmd = commodity.upper()
    # R11: cancelled flow never counts, so the tape side of this comparison is
    # CLEAN. ICE excludes busted prints from official volume too, so counting
    # them here produced a tape total that could exceed settle and trip a
    # false 'suspect_capture'. Rule owned by classifier.EXCLUDED_FROM_CLEAN.
    #
    # VINTAGE: both sides of this comparison are now SAME-DAY whenever the
    # settle file carries CumVolume (captures from the 2026-08-24 cutover on).
    # Pre-cutover files have only the legacy Volume column, which holds the
    # PRIOR session's figure -- for those the comparison is still skewed by one
    # session, and _read_settle_volumes reports vintage='prior_session' so the
    # skew is visible rather than assumed away. Do NOT shift dates to
    # compensate: the vintage flag is the honest signal.
    ex, exp = excluded_sql()
    tape = dict(db.q(
        'SELECT ice_code, SUM(size) FROM ticks'
        ' WHERE commodity=%s AND session_date=%s' + ex + ' GROUP BY ice_code',
        [cmd, session_date] + exp))
    settle, vintage = {}, 'none'
    if settle_path is not None and Path(settle_path).is_file():
        settle, vintage = _read_settle_volumes(Path(settle_path), cmd)
    if vintage == 'prior_session':
        print(f'NOTE: {cmd} {session_date} settle volume is PRIOR-SESSION '
              '(pre-cutover file, no CumVolume column) -- reconcile deltas '
              'carry a one-session skew.', file=sys.stderr)

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
