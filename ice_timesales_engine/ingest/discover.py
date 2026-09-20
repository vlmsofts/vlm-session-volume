"""
discover.py -- locate day-folders and blotter files under the READ-ONLY ICE root.

Naming (verified on disk):
  <ICE_ROOT>\\<CMD>\\<YYYY-MM-DD>\\futures_blotter_<CMD>_<FWD>_<YYYY-MM-DD>.csv
A forward's file exists only if it traded that day (capture skip-probes
zero-volume months by design) -- a missing file is ZERO volume, not a gap.
"""

import os
import re
import time
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


# -- capture-landed discriminator -------------------------------------------
# Added 2026-09-20, tightened the same day after audit.
#
# "No futures blotters" has two causes that were previously indistinguishable
# (see jobs/daily_ingest.py's module docstring): a real no-trade day, and a
# capture that has not finished writing yet.
#
# WHAT IS CHECKED, exactly, all three required:
#   1. the day folder exists;
#   2. it contains at least one file;
#   3. the NEWEST file in it was last modified at least CAPTURE_QUIESCE_SECONDS
#      ago (default 15 min) -- i.e. the capture has gone quiet.
#
# Condition 3 is the point. The capture writes settle / spreads /
# settled_surface / blotter files progressively, so a folder holding a single
# settle file while the blotters are still streaming satisfies 1 and 2 while
# the capture is very much still running. Without the quiescence test
# daily_ingest would read that as "landed", record a permanent no_blotter and
# exit 0 -- reintroducing the exact silent-zero this discriminator exists to
# prevent, just through a narrower window.
#
# RESIDUAL WINDOW, stated honestly: this is a heuristic, not a handshake. A
# capture that stalls for more than CAPTURE_QUIESCE_SECONDS mid-run and then
# resumes still reads as landed during the stall. The gap that leaves is
# bounded and self-healing -- jobs/catchup_ingest.py re-checks the last N
# trading days for blotters-on-disk-but-not-in-DB, so a day mis-classified
# this way is picked up on the next catch-up pass rather than lost. The only
# way to close it completely is a completion marker written by the capture
# itself, which lives in another repo (C:\Ice eod records) and is a
# cross-repo change, not a same-repo fix.

CAPTURE_QUIESCE_SECONDS = 15 * 60


def capture_landed(commodity: str, session_date: str,
                   now: Optional[float] = None,
                   quiesce_seconds: int = CAPTURE_QUIESCE_SECONDS) -> bool:
    """True when the capture demonstrably ran AND has gone quiet.

    `now` (epoch seconds) is injectable so the quiescence rule can be tested
    without sleeping; it defaults to the wall clock.
    """
    day_dir = config.blotter_dir(commodity.upper(), session_date)
    if not os.path.isdir(day_dir):
        return False
    mtimes = []
    for name in os.listdir(day_dir):
        full = os.path.join(day_dir, name)
        try:
            if os.path.isfile(full):
                mtimes.append(os.path.getmtime(full))
        except OSError:
            # A file vanishing mid-listing is itself evidence of an active
            # capture; ignore it and let the remaining mtimes decide.
            continue
    if not mtimes:
        return False
    clock = time.time() if now is None else now
    return (clock - max(mtimes)) >= quiesce_seconds
