"""
blotter_parser.py -- read one futures blotter CSV -> typed RawTick rows.

Verified header: Contract,Exchange Time,Price,Size,Conditions,Seq Num
Files under the ICE root are opened READ-ONLY ('r') -- never written.
"""

import csv
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

EXPECTED_COLS = ['Contract', 'Exchange Time', 'Price', 'Size', 'Conditions', 'Seq Num']


@dataclass
class RawTick:
    contract: str
    exchange_time: str
    price: float
    size: float
    conditions: str
    seq_num: int


def read_blotter(path: Path) -> Iterator[RawTick]:
    """Stream RawTicks from one blotter file.

    Fail-loud on schema drift (missing expected column). Tolerant of blank
    Conditions ('' is a real bucket) and skips fully blank lines. A malformed
    row is skipped with a WARNING, never fatal (mirrors sidecar-reader policy).
    """
    with open(path, 'r', newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        missing = ([c for c in EXPECTED_COLS if c not in fields]
                   if fields is not None else list(EXPECTED_COLS))
        if missing:
            raise ValueError(
                f'Blotter schema drift in {path.resolve()}: missing column(s) '
                f'{missing}. Expected {EXPECTED_COLS}; found {fields}.'
            )
        for line_no, row in enumerate(reader, start=2):
            if not any((v or '').strip() for v in row.values()):
                continue   # fully blank line
            try:
                yield RawTick(
                    contract=(row['Contract'] or '').strip(),
                    exchange_time=(row['Exchange Time'] or '').strip(),
                    price=float(row['Price']) if row['Price'] not in (None, '') else 0.0,
                    size=float(row['Size']),
                    conditions=(row['Conditions'] or '').strip(),
                    seq_num=int(float(row['Seq Num'])),
                )
            except (TypeError, ValueError, KeyError) as exc:
                print(f'WARNING: skipping malformed blotter row {path.name}:{line_no}: {exc}',
                      file=sys.stderr)


def file_sha256(path: Path) -> str:
    """Content hash for ingest_log incremental skip."""
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()
