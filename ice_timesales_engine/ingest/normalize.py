"""
normalize.py -- contract normalization, ET parsing, window tagging.

Blotter writes 'CT Z26' (space + 2-digit year); the reused contract_resolver
expects 'CTZ6' (no space, single-digit year). Verified mapping this session:
  CT Z26->CTDEC1, CT H27->CTMAR1, CT K27->CTMAY1, CT N27->CTJUL1,
  CT Z27->CTDEC2, CT V26->None (Oct), CT Q26->None (Aug).

TAS (added 2026-09-21): ICE captures Trade-At-Settlement on a symbol one
letter longer than the outright -- CT's TAS trades as 'CTZ', e.g.
'CTZ Z26' -> normalize_contract -> 'CTZZ6'. This ice_code is DELIBERATELY
DISTINCT from the outright's 'CTZ6': the two are separately sequenced by ICE
(confirmed on 2026-09-18: zero seq_num overlap between the outright and TAS
files for the same contract-day, but both draw from the same exchange-wide
counter, so a distinct ice_code is what makes a PK collision on
(commodity, session_date, ice_code, seq_num) structurally impossible rather
than merely unobserved). generic_code is still resolved against the
UNDERLYING contract ('CTZ6', not 'CTZZ6') via underlying_ice_code() below, so
TAS volume rolls up into the same CTDEC1/etc. buckets an outright would.

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
    """'CT Z26' -> 'CTZ6' (strip spaces, collapse 2-digit year to last digit).
    'CTZ Z26' -> 'CTZZ6' unchanged by this function -- see the module
    docstring for why the doubled letter is kept, not collapsed."""
    b = raw.replace(' ', '').upper().strip()
    if len(b) < 4 or not b[-2:].isdigit():
        raise ValueError(f'Unrecognized blotter contract: {raw!r}')
    return b[:-2] + b[-1]


# Commodities with a confirmed ICE Trade-At-Settlement symbol, and the extra
# letter ICE inserts after the commodity prefix ('CTZ Z26' -> normalize ->
# 'CTZZ6'). Never guessed -- add a commodity here only once its TAS blotter
# naming is confirmed on disk, same discipline as _HISTORICAL_FND in
# expiry_source.py. THE ONLY COPY OF THIS FACT: discover.py's TAS filename
# pattern and daily_ingest.py's TAS ice_code construction both call
# tas_symbol() below rather than hand-writing the letter a second time.
_TAS_LETTER = {'CT': 'Z'}


def tas_symbol(commodity: str) -> Optional[str]:
    """This commodity's TAS blotter symbol ('CT' -> 'CTZ'), or None if no
    TAS convention is confirmed for it yet."""
    letter = _TAS_LETTER.get(commodity.upper())
    return f'{commodity.upper()}{letter}' if letter else None


def is_tas_ice_code(ice_code: str, commodity: str) -> bool:
    """True if `ice_code` (already normalize_contract'd) is this commodity's
    Trade-At-Settlement symbol.

    A LENGTH check, not a prefix check: 'CTZ6' (outright Dec) legitimately
    STARTS WITH the same 'CTZ' a naive prefix test would use for TAS, so
    prefix-matching misclassifies every real December outright as TAS. TAS
    always carries exactly one extra character -- the inserted TAS letter --
    so the outright is always len(commodity)+2 and TAS is always
    len(commodity)+3 (month letter + 1-digit year, in both cases)."""
    cmd = commodity.upper()
    letter = _TAS_LETTER.get(cmd)
    if not letter:
        return False
    code = ice_code.upper()
    return (len(code) == len(cmd) + 3
            and code.startswith(cmd)
            and code[len(cmd)] == letter)


def underlying_ice_code(ice_code: str, commodity: str) -> str:
    """The outright ice_code a TAS row's volume should roll up under
    ('CTZZ6' -> 'CTZ6'). Returns `ice_code` unchanged for a non-TAS code
    (including a real 'CTZ6' outright), so it is always safe to call."""
    if not is_tas_ice_code(ice_code, commodity):
        return ice_code
    cmd = commodity.upper()
    return cmd + ice_code[len(cmd) + 1:]     # drop the inserted TAS letter


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
    """Generic slot for an in-universe contract, else None (excluded month/pos>=3).
    Uses THIS commodity's real active months (commodity_meta.COMMODITY_MONTHS)
    -- fixed 2026-07-31: previously always used CT's H/K/N/Z regardless of
    `commodity`, silently NULLing generic_code for every KC/CC Sep(U) and
    SB Oct(V) trade since ingestion began (backfilled in the same pass, see
    MEMORY.md).

    A TAS ice_code ('CTZZ6') is unwrapped to its underlying ('CTZ6') before
    resolving -- the resolver has never heard of the TAS symbol, and TAS
    volume is meant to roll up under the same generic (e.g. CTDEC1) an
    outright in that contract-month would."""
    from commodity_meta import COMMODITY_MONTHS
    cmd = commodity.upper()
    resolve_code = underlying_ice_code(ice_code, cmd)
    try:
        info = ice_to_generic(resolve_code, session_date, prefix=cmd,
                              active_months=COMMODITY_MONTHS.get(cmd))
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
    """RawTick -> NormTick (normalize + classify + window-tag).

    TAS-ness is derived from ice_code alone (is_tas_ice_code), never passed
    in by the caller -- the blotter row itself carries the fact (via which
    symbol it names), so there is nothing for daily_ingest to get wrong by
    reading the wrong file for the wrong flag."""
    ice_code = normalize_contract(raw.contract)
    et = parse_et(raw.exchange_time)
    is_tas = is_tas_ice_code(ice_code, commodity)
    return NormTick(
        commodity=commodity.upper(),
        session_date=session_date,
        ice_code=ice_code,
        generic_code=to_generic(ice_code, session_date, commodity),
        exchange_time=raw.exchange_time,
        price=raw.price,
        size=raw.size,
        primary_type=primary_type(tokenize(raw.conditions), is_tas=is_tas),
        conditions_raw=raw.conditions,
        seq_num=raw.seq_num,
        window_preset=assign_window(et, date.fromisoformat(session_date)),
    )
