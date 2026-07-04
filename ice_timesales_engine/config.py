"""
config.py -- ice_timesales_engine configuration.

Reuses (imports, never re-hardcodes) the session windows, holiday calendar and
contract symbology from VLM_Session_Volume_Project. New constants here are
limited to: ICE data-root paths, DB config, commodity parametrization, and this
repo's own output paths.

HARD RULE: the ICE root (C:\\Ice eod records\\) is READ-ONLY. Every derived
artifact is written elsewhere (Postgres/SQLite via DATABASE_URL, this repo's
data/ dir, logs/).
"""

import os
import sys

# ---------------------------------------------------------------------------
# REUSED SOURCE OF TRUTH (VLM_Session_Volume_Project) -- import, don't copy
# ---------------------------------------------------------------------------

# This engine lives at <VLM_Session_Volume_Project>/ice_timesales_engine/, so the
# VLM repo is the PARENT directory. Derive it relatively (survives a folder
# rename/move) but allow an env-var override for out-of-tree layouts.
_DEFAULT_VLM_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VLM_SESSION_VOLUME_REPO = os.environ.get('VLM_SESSION_VOLUME_REPO', _DEFAULT_VLM_REPO)
if VLM_SESSION_VOLUME_REPO not in sys.path:
    # Appended (not inserted at 0) so THIS repo's own modules win name clashes;
    # the VLM repo's config is loaded explicitly by file path below.
    sys.path.append(VLM_SESSION_VOLUME_REPO)

# This module is itself named `config`, so `from config import ...` would hit
# sys.modules['config'] (this half-initialized module). Load the VLM repo's
# config explicitly by file path under a distinct module name instead.
import importlib.util as _ilu                           # noqa: E402

_vlm_cfg_path = os.path.join(VLM_SESSION_VOLUME_REPO, 'config.py')
_spec = _ilu.spec_from_file_location('vlm_session_volume_config', _vlm_cfg_path)
_vlm = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_vlm)
sys.modules['vlm_session_volume_config'] = _vlm

# Re-export the reused constants so the rest of this repo imports from HERE.
OVERNIGHT_START_HH_MM = _vlm.OVERNIGHT_START_HH_MM     # (21, 0)  PREVIOUS calendar day
OVERNIGHT_END_HH_MM = _vlm.OVERNIGHT_END_HH_MM         # (7, 0)   session date
DAY_START_HH_MM = _vlm.DAY_START_HH_MM                 # (7, 0)
DAY_END_HH_MM = _vlm.DAY_END_HH_MM                     # (14, 20)
CT_CLOSED_DATES = _vlm.CT_CLOSED_DATES                 # frozenset 'YYYY-MM-DD' (IFUS)

# contract_resolver.py (reused from the VLM repo) does
#   `from config import CT_ACTIVE_MONTH_CODES, CT_EXCLUDED_PREFIXES, CT_SERIAL_TO_FUTURES`
# and, imported from THIS engine, `config` resolves to THIS module -- so those
# names must exist here too. Re-exported verbatim from the VLM config.
CT_ACTIVE_MONTH_CODES = _vlm.CT_ACTIVE_MONTH_CODES     # frozenset {'H','K','N','Z'}
CT_EXCLUDED_PREFIXES = _vlm.CT_EXCLUDED_PREFIXES       # frozenset {'CTV','CTQ'}
CT_SERIAL_TO_FUTURES = _vlm.CT_SERIAL_TO_FUTURES       # {'CTU':'CTZ','CTX':'CTZ','CTF':'CTH'}

# ---------------------------------------------------------------------------
# ICE DATA ROOT (READ-ONLY source of truth)
# ---------------------------------------------------------------------------

ICE_ROOT = os.environ.get('ICE_ROOT', r'C:\Ice eod records')

# Per-commodity closed-dates. CT is authoritative from the reused config; the
# softs trade the same IFUS holiday calendar -- reuse until a per-commodity
# calendar is sourced.
CLOSED_DATES = {
    'CT': CT_CLOSED_DATES,
    'KC': CT_CLOSED_DATES,
    'CC': CT_CLOSED_DATES,
    'SB': CT_CLOSED_DATES,
}

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
# DATABASE_URL:
#   postgres://...  or postgresql://...  -> psycopg (Supabase in production)
#   anything else / unset                -> local SQLite file (dev/test)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(REPO_ROOT, 'data')
LOG_DIR = os.path.join(REPO_ROOT, 'logs')

DEFAULT_SQLITE_PATH = os.path.join(DATA_DIR, 'ice_timesales.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ---------------------------------------------------------------------------
# ROLLUP SIDECAR FILES (this repo's data/history -- NOT the shared history
# files in VLM_Session_Volume_Project; blast-radius gate, build plan section 7)
# ---------------------------------------------------------------------------

HISTORY_DIR = os.path.join(DATA_DIR, 'history')
ROLLUP_SESSION_CSV = os.path.join(HISTORY_DIR, 'futures_session_volume_history_ICE.csv')
ROLLUP_BYCONTRACT_CSV = os.path.join(HISTORY_DIR, 'futures_session_volume_history_by_contract_ICE.csv')

# ---------------------------------------------------------------------------
# CLOUDFLARE (edge cache purge -- optional; no-op when unset)
# ---------------------------------------------------------------------------

CF_ZONE_ID = os.environ.get('CF_ZONE_ID', '')
CF_API_TOKEN = os.environ.get('CF_API_TOKEN', '')

# Cache TTLs (seconds) surfaced via Cache-Control
CACHE_TTL_CLOSED_SESSION = 86400 * 30   # past sessions are immutable
CACHE_TTL_LATEST_SESSION = 300          # latest session may still re-ingest


def blotter_dir(commodity: str, session_date: str) -> str:
    """Day folder for a commodity, e.g. C:\\Ice eod records\\CT\\2026-07-02."""
    return os.path.join(ICE_ROOT, commodity.upper(), session_date)
