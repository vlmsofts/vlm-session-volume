"""
discover.py -- locate day-folders and blotter files under the READ-ONLY ICE root.

Naming (verified on disk):
  <ICE_ROOT>\\<CMD>\\<YYYY-MM-DD>\\futures_blotter_<CMD>_<FWD>_<YYYY-MM-DD>.csv
A forward's file exists only if it traded that day (capture skip-probes
zero-volume months by design) -- a missing file is ZERO volume, not a gap.
"""

import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

import config

_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def find_day_folders(commodity: str,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None) -> list[str]:
    """Sorted list of 'YYYY-MM-DD' day-folder names present for a commodity.
    Missing commodity root -> empty list (caller logs zero, never errors)."""
    root = os.path.join(config.ICE_ROOT, commodity.upper())
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        if not _DAY_RE.match(name):
            continue
        if not os.path.isdir(os.path.join(root, name)):
            continue
        if date_from and name < date_from:
            continue
        if date_to and name > date_to:
            continue
        out.append(name)
    return sorted(out)


def find_blotter_files(commodity: str, session_date: str) -> list[Path]:
    """All futures blotter files for one commodity-day, sorted by forward."""
    cmd = commodity.upper()
    day_dir = config.blotter_dir(cmd, session_date)
    if not os.path.isdir(day_dir):
        return []
    pat = re.compile(
        rf'^futures_blotter_{cmd}_([A-Z]\d{{2}})_{re.escape(session_date)}\.csv$'
    )
    out = [Path(day_dir) / n for n in os.listdir(day_dir) if pat.match(n)]
    return sorted(out)


def parse_fwd_from_filename(path: Path) -> str:
    """futures_blotter_CT_Z26_2026-07-02.csv -> 'Z26'."""
    m = re.match(r'^futures_blotter_[A-Z]+_([A-Z]\d{2})_', path.name)
    if not m:
        raise ValueError(f'Not a futures blotter filename: {path.name}')
    return m.group(1)


def settle_path(commodity: str, session_date: str) -> Optional[Path]:
    p = Path(config.blotter_dir(commodity, session_date)) / f'futures_settle_{session_date}.csv'
    return p if p.is_file() else None


def spreads_path(commodity: str, session_date: str) -> Optional[Path]:
    p = Path(config.blotter_dir(commodity, session_date)) / f'spreads_{session_date}.csv'
    return p if p.is_file() else None
