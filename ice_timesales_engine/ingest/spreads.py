"""
spreads.py -- spreads_<date>.csv Block Volume -> block_supplement.

Block volume lives in TWO places (verified): rare 'BlockTrde*' prints on the
futures tape, and the 'Block Volume' column of spreads_*.csv. Both are stored
in block_supplement (source='tape' | 'spreads'), surfaced SEPARATELY, and
NEVER added into the tape total by default (avoids double count).

Spread names are 'CT Z26:CTH27' (verified) -- Block Volume is attributed to
BOTH legs as informational context.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

from store.db import Db, now_iso

from .normalize import normalize_contract


def _legs(spread_name: str, commodity: str):
    """'CT Z26:CTH27' -> ['CTZ6', 'CTH7'] (normalized ice codes)."""
    out = []
    for part in spread_name.split(':'):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(normalize_contract(part))
        except ValueError:
            print(f'WARNING: unparseable spread leg {part!r} in {spread_name!r}',
                  file=sys.stderr)
    return out


def ingest_block_volume(db: Db, commodity: str, session_date: str,
                        spreads_path: Path) -> int:
    """Load per-leg spread Block Volume + tape BlockTrde sums. Returns rows."""
    cmd = commodity.upper()

    # source='spreads': Block Volume column, attributed to each leg.
    per_leg = defaultdict(float)
    if spreads_path is not None and Path(spreads_path).is_file():
        with open(spreads_path, 'r', newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                try:
                    bv = float(row.get('Block Volume') or 0)
                except (TypeError, ValueError):
                    bv = 0.0
                if bv <= 0:
                    continue
                for leg in _legs(row.get('Spread', ''), cmd):
                    per_leg[leg] += bv

    # source='tape': sums of primary_type='block' prints already in ticks.
    tape_rows = db.q("""
        SELECT ice_code, SUM(size) FROM ticks
        WHERE commodity=%s AND session_date=%s AND primary_type='block'
        GROUP BY ice_code
    """, (cmd, session_date))

    db.exec("DELETE FROM block_supplement WHERE commodity=%s AND session_date=%s",
            (cmd, session_date))
    params = [(cmd, session_date, ice, vol, 'spreads', 0, now_iso())
              for ice, vol in sorted(per_leg.items())]
    params += [(cmd, session_date, ice, vol, 'tape', 1, now_iso())
               for ice, vol in tape_rows]
    if params:
        db.execmany("""
            INSERT INTO block_supplement (commodity, session_date, ice_code,
                                          block_volume, source, on_tape, generated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (commodity, session_date, ice_code, source) DO UPDATE SET
              block_volume=excluded.block_volume, on_tape=excluded.on_tape,
              generated_at=excluded.generated_at
        """, params)
    db.commit()
    return len(params)
