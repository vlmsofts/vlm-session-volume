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
with no settle file falls back to the Bloomberg backfill below, and if that
cannot serve it either the result is None and the caller must show that
honestly.

PRICE AUTHORITY IS BLOOMBERG, NOT THE ICE TAPE CAPTURE (Lou, 2026-09-02)
------------------------------------------------------------------------
The ICE eod root is a TIME & SALES capture, and ICE only retains time &
sales for a short window -- which is why 37 of its 92 CT day-folders are
marked _BACKFILL.txt, reconstructed on 2026-07-06 by backfill_blotter.py.
A reconstruction can only see contracts still listed on the day it runs,
so those folders silently omit months that had already expired (N26 and
K26 are absent from every May/June folder), and their OHLC columns are
not reliable for the session they name.

Settle/OHLC/volume, by contrast, is decades-deep reference data with no
retention limit. cotton_futures_volume_history.csv carries all 8 CT
generics from 2005-01-03 forward with px_last/high/low/open, volume and
open interest -- refreshed straight from the terminal by
cotton_futures_volume_history_blpapi.py.

So BLOOMBERG LEADS and ICE fills only what Bloomberg has not got yet
(today's session, before the history file is refreshed). This is not a
downgrade: measured 2026-09-02 across every ICE day-folder on disk, CTZ26
settle matched Bloomberg CTDEC1 px_last to within 0.005 on 87 of 87 days
-- 50 live, 37 backfilled, zero disagreements. They are the same number;
Bloomberg simply has 21 years of it and never drops an expired month.

Every returned row carries `source` ('bloomberg' | 'ice') so the origin is
always inspectable, though per Lou's ruling the chart does not label it.
Bloomberg carries no separate settle-vs-last distinction in this file, so
px_last IS the settle; OHLC comes from the same row.
"""

import csv
import os
import threading

import config
from contract_resolver import ice_to_generic, front_generic
from ingest.normalize import normalize_contract
from commodity_meta import COMMODITY_MONTHS


class PriceUnavailable(Exception):
    """Raised only for a genuinely broken read (e.g. ICE_ROOT unreachable) --
    NOT for an ordinary missing settle file (weekend, T+1 not yet written),
    which is a normal, honest empty result, not an error."""


# ---------------------------------------------------------------------------
# Bloomberg backfill index -- loaded once, lazily, under a lock.
# ---------------------------------------------------------------------------
# Shape: {commodity: {session_date: {generic_code: {settle,open,high,low}}}}.
# The file is a
# few MB and is read at most once per process; a missing/unreadable file is a
# normal absence (the backfill simply cannot serve), never a crash -- ICE
# remains the primary and must keep working on its own.

_BBG_LOCK = threading.Lock()
_BBG_INDEX = None


def _bbg_index() -> dict:
    """{commodity: {date: {generic_code: settle}}} from config.BBG_SETTLE_CSV.

    Read once per process behind a lock (the Flask app is threaded; two
    concurrent range requests must not both parse the file). Returns {} if the
    file is absent or unreadable -- the backfill is strictly additive, so its
    absence degrades to exactly the old ICE-only behaviour."""
    global _BBG_INDEX
    if _BBG_INDEX is not None:
        return _BBG_INDEX
    with _BBG_LOCK:
        if _BBG_INDEX is not None:      # another thread won the race
            return _BBG_INDEX
        idx = {}
        path = getattr(config, 'BBG_SETTLE_CSV', '')
        if path and os.path.isfile(path):
            try:
                with open(path, newline='', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        generic = (row.get('generic') or '').strip().upper()
                        date_s = (row.get('date') or '').strip()
                        raw = (row.get('px_last') or '').strip()
                        if not generic or not date_s or not raw:
                            continue
                        try:
                            px = float(raw)
                        except ValueError:
                            continue    # bad field is a missing field, never a crash

                        def _f(col):
                            v = (row.get(col) or '').strip()
                            try:
                                return float(v)
                            except ValueError:
                                return None

                        # Commodity is the generic's alpha prefix: CTDEC1 -> CT.
                        cmd = ''.join(ch for ch in generic[:2] if ch.isalpha())
                        if not cmd:
                            continue
                        idx.setdefault(cmd, {}).setdefault(date_s, {})[generic] = {
                            'settle': px, 'open': _f('px_open'),
                            'high': _f('px_high'), 'low': _f('px_low'),
                        }
            except (OSError, UnicodeDecodeError, csv.Error):
                idx = {}                # unreadable -> backfill unavailable, ICE unaffected
                                        # (a non-UTF-8 re-save must degrade, not 500)
        _BBG_INDEX = idx
    return _BBG_INDEX


def _ice_has_session(commodity: str, session_date: str) -> bool:
    """True if the ICE capture holds a settle FILE for this session.

    The Bloomberg gate is per-SESSION, never per-contract. A session ICE
    captured is ICE's to answer: a contract absent from that file is a real
    absence (rolled off the board, or a blank/'N/A' Settle that
    _read_settle_rows correctly skipped), and must render blank rather than
    silently borrow another vendor's price mid-series."""
    return os.path.isfile(_settle_file(commodity.upper(), session_date))


def _bbg_row(commodity: str, session_date: str, generic: str):
    """{'settle','open','high','low'} for one (commodity, date, generic_code),
    or None. Bloomberg's px_last IS the settle for these generics -- verified
    equal to ICE's Settle column on all 87 ICE day-folders on disk.

    Gated on BBG_SETTLE_COMMODITIES: the history file holds CT generics only,
    so KC/CC/SB never resolve here and fall through to their ICE capture
    rather than silently borrowing a cotton price."""
    cmd = commodity.upper()
    if cmd not in getattr(config, 'BBG_SETTLE_COMMODITIES', frozenset()):
        return None
    if not generic:
        return None
    return _bbg_index().get(cmd, {}).get(session_date, {}).get(generic.upper())


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
    """{'generic_code','ice_code','settle','open','high','low','date','source'}
    for one session, for a specific ice_code if given, else the front month.

    BLOOMBERG FIRST, ICE FILLS THE REST. The Bloomberg history is the price
    authority (see the module docstring): decades deep, never drops an expired
    contract, and verified identical to ICE's settle on all 87 day-folders on
    disk. The ICE tape capture answers only for a session Bloomberg has not
    got yet -- typically today's, before the history file is refreshed.

    Returns None when neither has it: an honest absence (weekend, exchange
    holiday, today not yet settled, or a commodity with no history), never a
    fabricated value.
    """
    cmd = commodity.upper()
    if ice_code:
        info = ice_to_generic(ice_code, session_date, prefix=cmd,
                              active_months=COMMODITY_MONTHS.get(cmd))
        generic = info.generic_code if info else None
    else:
        generic = front_month_generic(cmd, session_date)
    if not generic:
        return None

    row = _bbg_row(cmd, session_date, generic)
    if row is not None:
        return {'generic_code': generic, 'ice_code': ice_code or generic,
                'settle': row['settle'], 'open': row['open'],
                'high': row['high'], 'low': row['low'],
                'date': session_date, 'source': 'bloomberg'}

    # Bloomberg has not got this session yet -- fall through to the ICE
    # capture, which is fresh the moment the eod job writes it.
    rows = _read_settle_rows(cmd, session_date)
    r = rows.get(generic)
    if r is None:
        return None
    return {'generic_code': generic, 'ice_code': ice_code or r['ice_code'],
            'settle': r['settle'], 'open': r['open'], 'high': r['high'],
            'low': r['low'], 'date': session_date, 'source': 'ice'}


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
                   'open': row['open'], 'high': row['high'], 'low': row['low'],
                   'source': row.get('source')}
                  if row else None)
    return out, errored
