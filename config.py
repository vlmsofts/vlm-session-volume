"""
config.py — Self-contained configuration for vlm_session_volume.
Imports NOTHING from Options_flow_analyzer. All paths, windows, tiers defined here.
"""

import os

# ---------------------------------------------------------------------------
# SOURCE TAPE (read-only path into the old repo's data folder)
# ---------------------------------------------------------------------------

OPTIONS_FLOW_DATA = r"C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\Options_flow_analyzer\data"

# ---------------------------------------------------------------------------
# SESSION WINDOWS (ET = Eastern Time, as recorded on the tape timestamps)
# ---------------------------------------------------------------------------

# Overnight: 21:00 previous calendar day → 07:00 session date
OVERNIGHT_START_HH_MM = (21, 0)   # on the PREVIOUS calendar day
OVERNIGHT_END_HH_MM   = (7,  0)   # on the session date  — last snapshot <= this

# Day: 07:00 → 14:20 session date  (day vol = cumulative@14:20 − cumulative@07:00)
DAY_START_HH_MM = (7,  0)
DAY_END_HH_MM   = (14, 20)

# ---------------------------------------------------------------------------
# LOOKBACK TIERS
# ---------------------------------------------------------------------------

LOOKBACK_TIERS = (5, 10, 20, 30, 60)   # sessions

# ---------------------------------------------------------------------------
# CAPTURE UNIVERSE — exactly 8 slots, locked
# ---------------------------------------------------------------------------

# ICE futures month codes for the four active CT delivery months (Oct/Aug excluded).
CT_ACTIVE_MONTH_CODES = frozenset(['H', 'K', 'N', 'Z'])   # Mar May Jul Dec

# Month-word → (month_number, ICE_letter, month_name)
CT_MONTH_META = {
    'MAR': (3,  'H', 'Mar'),
    'MAY': (5,  'K', 'May'),
    'JUL': (7,  'N', 'Jul'),
    'DEC': (12, 'Z', 'Dec'),
}

# Generic positions captured: 1 and 2 only  → 8 slots total
GENERIC_POSITIONS = (1, 2)

# Excluded ICE 3-char prefixes (Oct=CTV, Aug=CTQ) — matches existing repo frozenset
CT_EXCLUDED_PREFIXES = frozenset(['CTV', 'CTQ'])

# ---------------------------------------------------------------------------
# CT SERIAL OPTIONS → PARENT FUTURES MAPPING
# (from Options_flow_analyzer/config/settings.py — copied, not imported)
# Serials not in this map AND not a direct active-month future are dropped.
# ---------------------------------------------------------------------------

CT_SERIAL_TO_FUTURES = {
    'CTU': 'CTZ',   # September options  → December futures
    'CTX': 'CTZ',   # November options   → December futures
    'CTF': 'CTH',   # January options    → March futures
}

# ---------------------------------------------------------------------------
# CT EXCHANGE HOLIDAYS 2026 (source: IFUS Trading Hours & Holiday Calendar 2026)
# ICE Futures U.S. — Cotton No. 2 closed dates
# ---------------------------------------------------------------------------

CT_CLOSED_DATES = frozenset([
    '2026-01-01',   # New Year's Day
    '2026-01-19',   # Martin Luther King Jr. Day
    '2026-02-16',   # Presidents' Day
    '2026-04-03',   # Good Friday
    '2026-05-25',   # Memorial Day
    '2026-06-19',   # Juneteenth National Independence Day
    '2026-07-03',   # Independence Day (observed)
    '2026-09-07',   # Labor Day
    '2026-11-26',   # Thanksgiving Day
    '2026-12-25',   # Christmas Day
])

# ---------------------------------------------------------------------------
# OUTPUT PATHS (within this repo)
# ---------------------------------------------------------------------------

REPO_ROOT    = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(REPO_ROOT, 'data')
HISTORY_DIR  = os.path.join(DATA_DIR, 'history')
HISTORY_CSV  = os.path.join(HISTORY_DIR, 'session_volume_history.csv')

def session_output_dir(date_str):
    return os.path.join(DATA_DIR, date_str)

def session_txt_path(date_str):
    return os.path.join(session_output_dir(date_str), 'session_volume.txt')

def session_json_path(date_str):
    return os.path.join(session_output_dir(date_str), 'session_volume.json')

# ---------------------------------------------------------------------------
# TAPE PATH HELPER  (reads from the OLD repo — read-only)
# ---------------------------------------------------------------------------

def options_tape_path(date_str, commodity='CT'):
    return os.path.join(OPTIONS_FLOW_DATA, date_str,
                        f'{commodity.lower()}_options_tape.csv')

# ---------------------------------------------------------------------------
# FUTURES SESSION-VOLUME (Part B)  — sidecar + history paths, VLM API source
# ---------------------------------------------------------------------------

# Sidecar futures-volume capture written by Options_flow_analyzer/price_tape.py
# (Part A). Read-only here. Schema: timestamp,date,commodity,contract,boundary,
# volume,oi  — boundary in {open, 0700, 1420}.
def futures_sidecar_path(date_str, commodity='CT'):
    return os.path.join(OPTIONS_FLOW_DATA, date_str,
                        f'{commodity.lower()}_futures_volume.csv')

# Boundary labels (must match price_tape.py's _SIDECAR_BOUNDARY_DEFS labels).
FUT_BOUNDARIES = ('open', '0700', '1420')

# Permanent, append-only futures history (this repo). One session-level file +
# a per-contract companion carrying the symbology key (for Dec-over-years).
FUT_HISTORY_CSV          = os.path.join(HISTORY_DIR, 'futures_session_volume_history.csv')
FUT_HISTORY_BY_CONTRACT  = os.path.join(HISTORY_DIR, 'futures_session_volume_history_by_contract.csv')

def fut_session_txt_path(date_str):
    return os.path.join(session_output_dir(date_str), 'futures_session_volume.txt')

def fut_session_json_path(date_str):
    return os.path.join(session_output_dir(date_str), 'futures_session_volume.json')

# Full-session source (LOCKED): Bloomberg PX_VOLUME — authoritative, verified
# vs the ICE official daily report (12-Jun-2026: thin months exact, active fronts
# within ~2-4%, gap = ICE TAS/TIC vs bbg outright). oi_data.csv is NOT used as
# the history source (its volume is low-vintage, only ~2 weeks deep).
#
# Deep seed: one-time Bloomberg HistoricalDataRequest, flattened to long format
# (date, generic, ticker, volume, open_int, px_*, ...), 8 generics, 2005->present.
FUT_SEED_CSV = os.path.join(REPO_ROOT, 'cotton_futures_volume_history.csv')

# oi_data passthrough kept ONLY as a recent-weeks cross-check (not history source).
VLM_API_BASE        = 'https://vlmapi.vlmdata.com'
VLM_OI_DATA_PATH    = '/v1/github/oi-dashboard/data/oi_data.csv'
VLM_API_KEY_ENV     = 'VLM_API_KEY'
