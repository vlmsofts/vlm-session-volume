"""
normalize.py -- contract normalization, ET parsing, window tagging.

Blotter writes 'CT Z26' (space + 2-digit year); the reused contract_resolver
expects 'CTZ6' (no space, single-digit year). Verified mapping this session:
  CT Z26->CTDEC1, CT H27->CTMAR1, CT K27->CTMAY1, CT N27->CTJUL1,
  CT Z27->CTDEC2, CT V26->None (Oct), CT Q26->None (Aug).

Window rule ([start, end) half-open, ET wall-clock, from reused config):
  night = [session_date-1 21:00, session_date 07:00)
  day   = [session_date 07:00,   session_date 14:20)
  other = anything else (e.g. a print at/after 14:20)
A 07:00:00 tick is DAY. Session-date pre-07:00 ticks are NIGHT.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

import config
from contract_resolver import ice_to_generic

from .classifier import primary_type, tokenize


def normalize_contract(raw: str) -> str:
    """'CT Z26' -> 'CTZ6' (strip spaces, collapse 2-digit year to last digit)."""
    b = raw.replace(' ', '').upper().strip()
    if len(b) < 4 or not b[-2:].isdigit():
        raise ValueError(f'Unrecognized blotter contract: {raw!r}')
    return b[:-2] + b[-1]


def parse_et(ts: str) -> datetime:
    """ISO string without TZ suffix -> naive ET datetime (stored as-is)."""
    return datetime.fromisoformat(ts)


def window_bounds(session_date: date) -> dict:
    """Concrete naive-ET boundaries for one session date, from reused config."""
    prev = session_date - timedelta(days=1)
    return {
        'night_start': datetime.combine(prev, time(*config.OVERNIGHT_START_HH_MM)),
        'night_end': datetime.combine(session_date, time(*config.OVERNIGHT_END_HH_MM)),
        'day_start': datetime.combine(session_date, time(*config.DAY_START_HH_MM)),
        'day_end': datetime.combine(session_date, time(*config.DAY_END_HH_MM)),
    }


def assign_window(exchange_time: datetime, session_date: date) -> str:
    """night | day | other for one tick, half-open [start, end)."""
    b = window_bounds(session_date)
    if b['night_start'] <= exchange_time < b['night_end']:
        return 'night'
    if b['day_start'] <= exchange_time < b['day_end']:
        return 'day'
    return 'other'


def to_generic(ice_code: str, session_date: str, commodity: str) -> Optional[str]:
    """Generic slot for an in-universe contract, else None (Oct/Aug/pos>=3).
    contract_resolver is CT-parametric today; non-CT returns None (softs
    resolver extension is a flagged follow-on, build plan section 8)."""
    try:
        info = ice_to_generic(ice_code, session_date, prefix=commodity.upper())
    except Exception:
        return None
    return info.generic_code if info else None


@dataclass
class NormTick:
    commodity: str
    session_date: str          # 'YYYY-MM-DD'
    ice_code: str              # 'CTZ6'
    generic_code: Optional[str]
    exchange_time: str         # ISO naive ET (stored verbatim)
    price: float
    size: float
    primary_type: str
    conditions_raw: str
    seq_num: int
    window_preset: str         # night | day | other


def normalize_tick(raw, commodity: str, session_date: str) -> NormTick:
    """RawTick -> NormTick (normalize + classify + window-tag)."""
    ice_code = normalize_contract(raw.contract)
    et = parse_et(raw.exchange_time)
    return NormTick(
        commodity=commodity.upper(),
        session_date=session_date,
        ice_code=ice_code,
        generic_code=to_generic(ice_code, session_date, commodity),
        exchange_time=raw.exchange_time,
        price=raw.price,
        size=raw.size,
        primary_type=primary_type(tokenize(raw.conditions)),
        conditions_raw=raw.conditions,
        seq_num=raw.seq_num,
        window_preset=assign_window(et, date.fromisoformat(session_date)),
    )
