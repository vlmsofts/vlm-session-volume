"""
contract_resolver.py — Generic ↔ actual ICE contract resolution for vlm_session_volume.

Vendored + extended from Options_flow_analyzer/pipeline/contract_calendar.py.
That module is December-only; this extends to all four CT active months: H K N Z.

NEVER import from the old repo. This is self-contained.

Key design:
  - Calendar-locked generics CTDEC1/CTMAR2/CTMAY1/CTJUL2 etc. name "the Nth
    contract of that calendar month forward from the as-of date."
  - The generic NEVER dies — it represents the slot, not the contract.
  - Roll calendar: a futures month rolls off the generic board at its first-notice /
    expiry. For an indefinite history file we store:
      ice_code       e.g. CTZ6
      generic_code   e.g. CTDEC1
      delivery_year  e.g. 2026  (4-digit — decade-safe)
      month_code     e.g. Z
      month_name     e.g. Dec

Public API
----------
  resolve_generic(generic_code, as_of_date) -> ContractInfo
  ice_to_generic(ice_code, as_of_date)      -> ContractInfo | None
  parse_ice_code(ice_code)                  -> (prefix, month_letter, delivery_year_4digit)
  futures_prefix_for(option_ice_code)       -> str | None   (serial roll map)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from config import CT_ACTIVE_MONTH_CODES, CT_EXCLUDED_PREFIXES, CT_SERIAL_TO_FUTURES

# ---------------------------------------------------------------------------
# MONTH TABLES (self-contained — not imported from old repo)
# ---------------------------------------------------------------------------

# ICE single-letter month codes
_MONTH_LETTER = {
    1: 'F', 2: 'G',  3: 'H',  4: 'J',  5: 'K',  6: 'M',
    7: 'N', 8: 'Q',  9: 'U', 10: 'V', 11: 'X', 12: 'Z',
}
_LETTER_TO_MONTH = {v: k for k, v in _MONTH_LETTER.items()}

# Bloomberg generic month words → (month_number, month_name_short)
_WORD_META = {
    'JAN': (1,  'Jan'), 'FEB': (2,  'Feb'), 'MAR': (3,  'Mar'),
    'APR': (4,  'Apr'), 'MAY': (5,  'May'), 'JUN': (6,  'Jun'),
    'JUL': (7,  'Jul'), 'AUG': (8,  'Aug'), 'SEP': (9,  'Sep'),
    'OCT': (10, 'Oct'), 'NOV': (11, 'Nov'), 'DEC': (12, 'Dec'),
}

# Four active CT delivery months, their Bloomberg generic word, and ICE letter
_CT_ACTIVE = {
    3:  ('MAR', 'H', 'Mar'),
    5:  ('MAY', 'K', 'May'),
    7:  ('JUL', 'N', 'Jul'),
    12: ('DEC', 'Z', 'Dec'),
}

# ---------------------------------------------------------------------------
# DATA CLASS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractInfo:
    ice_code:      str    # e.g. CTZ6
    generic_code:  str    # e.g. CTDEC1
    prefix:        str    # e.g. CT
    month_code:    str    # e.g. Z
    month_name:    str    # e.g. Dec
    month_num:     int    # e.g. 12
    delivery_year: int    # 4-digit, e.g. 2026
    position:      int    # 1 or 2


# ---------------------------------------------------------------------------
# LOW-LEVEL HELPERS
# ---------------------------------------------------------------------------

def _coerce_date(d) -> date:
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def _ice_year_to_full(letter: str, digit_char: str, as_of: date) -> int:
    """Recover 4-digit delivery year from single-digit ICE year code.

    ICE encodes year as last digit only (CTZ6 = Dec 2026 or 2036…).
    We pick the nearest year >= as_of.year whose last digit matches,
    but cap the look-forward at 10 years so we never land a decade ahead
    when the contract is clearly the current or next year's.
    """
    month_num = _LETTER_TO_MONTH[letter]
    digit = int(digit_char)
    base = as_of.year
    # Try candidate years from base-1 to base+9
    for delta in range(-1, 11):
        candidate = base + delta
        if candidate % 10 == digit:
            # Sanity: the delivery month must not have already passed more than ~2 years ago
            candidate_date = date(candidate, month_num, 1)
            if candidate_date >= date(as_of.year - 2, 1, 1):
                return candidate
    # Fallback: base + offset
    offset = (digit - base % 10) % 10
    return base + offset


# ---------------------------------------------------------------------------
# PUBLIC: parse an ICE code
# ---------------------------------------------------------------------------

def parse_ice_code(ice_code: str, as_of_date=None) -> tuple[str, str, int]:
    """Parse 'CTZ6' → ('CT', 'Z', 2026).

    as_of_date is used to recover the 4-digit year from the single-digit ICE
    code. Defaults to today if not provided.
    """
    body = ice_code.strip().upper()
    # Last char = year digit, second-to-last = month letter, rest = prefix
    if len(body) < 3:
        raise ValueError(f'ICE code too short: {ice_code!r}')
    year_digit = body[-1]
    month_letter = body[-2]
    prefix = body[:-2]
    if month_letter not in _LETTER_TO_MONTH:
        raise ValueError(f'Unknown month letter {month_letter!r} in {ice_code!r}')
    if not year_digit.isdigit():
        raise ValueError(f'Year digit {year_digit!r} not numeric in {ice_code!r}')
    as_of = _coerce_date(as_of_date) if as_of_date else date.today()
    year_4 = _ice_year_to_full(month_letter, year_digit, as_of)
    return prefix, month_letter, year_4


# ---------------------------------------------------------------------------
# PUBLIC: resolve a Bloomberg generic to ContractInfo
# ---------------------------------------------------------------------------

def resolve_generic(generic_code: str, as_of_date) -> ContractInfo:
    """Resolve 'CTDEC1' on 2026-06-17 → ContractInfo(ice_code='CTZ6', delivery_year=2026, …).

    Logic: CTDEC1 = the 1st December >= as_of_date (i.e. the front December).
    CTMAR2 = the 2nd March >= as_of_date.

    Roll rule: a contract is "available" if its delivery month has NOT yet
    started (delivery_date = 1st of the delivery month).  Once that month
    begins, the generic rolls to the next year's contract.
    """
    as_of = _coerce_date(as_of_date)
    body = generic_code.strip().upper()

    # Parse: e.g. CTDEC1 → prefix=CT, word=DEC, position=1
    m = re.match(r'^([A-Z]+)([A-Z]{3})(\d+)$', body)
    if not m:
        raise ValueError(f'Not a generic month code: {generic_code!r}')
    prefix, word, pos_str = m.group(1), m.group(2), m.group(3)
    position = int(pos_str)

    if word not in _WORD_META:
        raise ValueError(f'Unknown generic month word {word!r} in {generic_code!r}')
    month_num, month_name = _WORD_META[word]
    month_letter = _MONTH_LETTER[month_num]

    # Find the Nth delivery year >= as_of where that month has not yet rolled
    # A month is "live on the generic board" until the 1st of the delivery month.
    count = 0
    year = as_of.year
    # Start from this year; if we've already passed the delivery month this year, skip ahead
    while True:
        delivery_start = date(year, month_num, 1)
        if delivery_start >= as_of:
            count += 1
            if count == position:
                break
        year += 1
        if year > as_of.year + 15:   # safety valve
            raise ValueError(f'Could not resolve {generic_code!r} from {as_of}')

    ice_code = f'{prefix}{month_letter}{year % 10}'
    return ContractInfo(
        ice_code=ice_code,
        generic_code=f'{prefix}{word}{position}',
        prefix=prefix,
        month_code=month_letter,
        month_name=month_name,
        month_num=month_num,
        delivery_year=year,
        position=position,
    )


# ---------------------------------------------------------------------------
# PUBLIC: find which generic slot an ICE actual code occupies on as_of_date
# ---------------------------------------------------------------------------

def ice_to_generic(ice_code: str, as_of_date,
                   prefix: str = 'CT') -> Optional[ContractInfo]:
    """Inverse of resolve_generic for the 8-slot capture universe.

    Returns None if the contract is not currently in positions 1 or 2 for any
    active CT month on as_of_date, or if its month is excluded.
    """
    as_of = _coerce_date(as_of_date)
    try:
        p, month_letter, delivery_year = parse_ice_code(ice_code, as_of)
    except ValueError:
        return None

    if p.upper() != prefix.upper():
        return None

    month_num = _LETTER_TO_MONTH.get(month_letter)
    if month_num is None:
        return None

    # Check excluded
    three_char_prefix = f'{p.upper()}{month_letter}'
    if three_char_prefix in CT_EXCLUDED_PREFIXES:
        return None

    # Must be an active CT month
    if month_num not in _CT_ACTIVE:
        return None

    word, _, month_name = _CT_ACTIVE[month_num]

    # Which position is this? Resolve positions 1 and 2 and see if either matches.
    generic_base = f'{prefix.upper()}{word}'
    for pos in (1, 2):
        info = resolve_generic(f'{generic_base}{pos}', as_of)
        if info.delivery_year == delivery_year:
            return info

    return None   # beyond position 2 — out of scope


# ---------------------------------------------------------------------------
# PUBLIC: serial option → parent futures prefix
# ---------------------------------------------------------------------------

def futures_prefix_for(option_ice_code: str) -> Optional[str]:
    """Map a serial option ICE code to its parent futures 3-char prefix.

    'CTU6' → 'CTZ'  (Sep options → Dec futures)
    'CTZ6' → 'CTZ'  (Dec options → Dec futures, direct month)
    Returns None if not mappable into the active universe.
    """
    body = option_ice_code.strip().upper()
    three = body[:3]

    # Direct map for serials
    if three in CT_SERIAL_TO_FUTURES:
        return CT_SERIAL_TO_FUTURES[three]

    # Direct delivery month — return its own prefix if active
    if len(body) >= 3:
        letter = body[2] if len(body) == 4 else body[-2]
        month_num = _LETTER_TO_MONTH.get(letter)
        if month_num in _CT_ACTIVE:
            return body[:2] + _MONTH_LETTER[month_num]  # e.g. CTZ

    return None


# ---------------------------------------------------------------------------
# BUILD THE 8-SLOT UNIVERSE for a given as-of date
# ---------------------------------------------------------------------------

def build_capture_universe(as_of_date) -> dict[str, ContractInfo]:
    """Return {generic_code: ContractInfo} for all 8 slots on as_of_date.

    Keys: CTMAR1, CTMAR2, CTMAY1, CTMAY2, CTJUL1, CTJUL2, CTDEC1, CTDEC2
    """
    as_of = _coerce_date(as_of_date)
    out = {}
    for _, (word, _, _) in sorted(_CT_ACTIVE.items()):
        for pos in (1, 2):
            code = f'CT{word}{pos}'
            info = resolve_generic(code, as_of)
            out[info.generic_code] = info
    return out
