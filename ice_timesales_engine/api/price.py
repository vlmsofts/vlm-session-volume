"""
price.py -- futures settlement price (+ OHLC), read from the ICE eod capture
root (config.ICE_ROOT, read-only -- see config.py's HARD RULE), NOT the VLM
gateway. Same root this engine already reads for its blotter ingest.

Each session day writes futures_settle_<date>.csv:
    Date,Contract,Settle,RecSet,PrevSettle,Change,Open,High,Low,Last,Volume,OpenInt
    2026-07-31,CT Z26,81.79,81.79,80.67,1.12,80.65,81.95,80.62,81.79,22592.0,194350.0

Local file read -- no network call, no cache-staleness window, same-day fresh
the moment ICE eod capture writes it (this engine's own blotter ingest reads
the SAME day-folder for volume). Never interpolated, never guessed: a date
with no settle file returns None and the caller must show that honestly.
"""

import csv
import os

import config
from contract_resolver import ice_to_generic, front_generic
from ingest.normalize import normalize_contract
from commodity_meta import COMMODITY_MONTHS


class PriceUnavailable(Exception):
    """Raised only for a genuinely broken read (e.g. ICE_ROOT unreachable) --
    NOT for an ordinary missing settle file (weekend, T+1 not yet written),
    which is a normal, honest empty result, not an error."""


def _settle_file(commodity: str, session_date: str) -> str:
    return os.path.join(config.blotter_dir(commodity, session_date),
                        f'futures_settle_{session_date}.csv')


def _read_settle_rows(commodity: str, session_date: str) -> dict:
    """{generic_code: {settle, open, high, low}} for one session day, keyed
    by THIS engine's own generic-code convention (via ice_to_generic on the
    file's 'CT Z26'-style Contract column). Empty dict if the file doesn't
    exist yet (weekend, holiday, or today's session not yet closed) --
    that's a normal state, not an error."""
    cmd = commodity.upper()
    path = _settle_file(cmd, session_date)
    if not os.path.isfile(path):
        return {}
    months = COMMODITY_MONTHS.get(cmd)
    out = {}
    try:
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                raw_contract = (row.get('Contract') or '').strip()
                if not raw_contract:
                    continue
                try:
                    ice_code = normalize_contract(raw_contract)
                except ValueError:
                    continue   # unrecognized contract token -- skip, don't crash the file
                info = ice_to_generic(ice_code, session_date, prefix=cmd, active_months=months)
                if not info:
                    continue   # not one of this commodity's real active months (e.g. Oct for CT)

                def _f(key):
                    v = (row.get(key) or '').strip()
                    if not v:
                        return None
                    try:
                        return float(v)
                    except ValueError:
                        return None   # e.g. 'N/A'/'UNCH' on a halted contract -- a bad
                                      # field is a missing field, never a crash

                settle = _f('Settle')
                if settle is None:
                    continue
                out[info.generic_code] = {
                    'ice_code': ice_code,
                    'settle': settle,
                    'open': _f('Open'),
                    'high': _f('High'),
                    'low': _f('Low'),
                }
    except OSError as exc:
        raise PriceUnavailable(f'could not read {path}: {exc}') from exc
    return out


def front_month_generic(commodity: str, session_date: str) -> str | None:
    """The generic code (e.g. 'CTDEC1') for the FRONT contract of `commodity`
    on `session_date` -- the nearest-expiring of THIS commodity's real active
    months (commodity_meta.COMMODITY_MONTHS; CT is H/K/N/Z only, October is
    never in that set and can never be selected here)."""
    cmd = commodity.upper()
    months = COMMODITY_MONTHS.get(cmd)
    if not months:
        return None
    info = front_generic(cmd, session_date, months)
    return info.generic_code if info else None


def settle_for(commodity: str, session_date: str, ice_code: str = None) -> dict | None:
    """{'generic_code', 'ice_code', 'settle', 'open', 'high', 'low', 'date'}
    for one session, for a specific ice_code if given, else the front month.
    None if that file/contract has no settle yet -- an honest absence
    (weekend, holiday, today not yet closed), never a fabricated value."""
    cmd = commodity.upper()
    rows = _read_settle_rows(cmd, session_date)
    if ice_code:
        info = ice_to_generic(ice_code, session_date, prefix=cmd,
                              active_months=COMMODITY_MONTHS.get(cmd))
        generic = info.generic_code if info else None
    else:
        generic = front_month_generic(cmd, session_date)
    if not generic or generic not in rows:
        return None
    r = rows[generic]
    return {'generic_code': generic, 'ice_code': ice_code or r['ice_code'],
            'settle': r['settle'], 'open': r['open'], 'high': r['high'],
            'low': r['low'], 'date': session_date}


def settle_series(commodity: str, dates: list, ice_code: str = None) -> tuple[dict, list]:
    """({date: {'generic_code','settle','open','high','low'} | None}, errored_dates)
    for a list of session dates. Front month is resolved PER DATE (the front
    contract can roll mid-range). One file read per date -- these are small
    local CSVs (a handful of contracts each), not a bulk fetch.

    A PriceUnavailable (unreadable file) on ONE date must never blank out
    every other date's real, successfully-read settle in the range -- caught
    per-date here. errored_dates lists which dates hit a genuine read error
    (as opposed to an honest "no file yet") so the caller can still surface
    that distinction rather than silently conflating the two."""
    cmd = commodity.upper()
    out, errored = {}, []
    for d in dates:
        try:
            row = settle_for(cmd, d, ice_code=ice_code)
        except PriceUnavailable:
            out[d] = None
            errored.append(d)
            continue
        out[d] = ({'generic_code': row['generic_code'], 'settle': row['settle'],
                   'open': row['open'], 'high': row['high'], 'low': row['low']}
                  if row else None)
    return out, errored
