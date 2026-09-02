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

SECOND SOURCE -- BLOOMBERG BACKFILL (CT only, config.BBG_SETTLE_CSV):
The ICE capture begins 2026-04-27; sessions before that have real archived
volume but no settle file, so the overlay was blank for every pre-capture
date. cotton_futures_volume_history.csv (the same Bloomberg pull that seeded
the volume archive) carries px_last keyed on the SAME generic_code convention
ice_to_generic produces, so the join is exact.

Precedence is ICE-first, always: Bloomberg is consulted ONLY for a
(date, contract) the ICE capture does not serve. A real ICE settle is never
overridden. Every returned row carries `source` ('ice' | 'bloomberg') so the
distinction survives in the API even though -- per Lou's 2026-09-02 ruling --
the chart does not render it: for a graphical overlay the two are close
enough, and precision here is not what the picture is for.

No fabrication: px_last is a real traded/settled Bloomberg print, not an
interpolation. Bloomberg carries no Open/High/Low in this file, so those stay
None on a backfilled row rather than being invented from the settle.
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
# Shape: {commodity: {session_date: {generic_code: px_last}}}. The file is a
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
                        # Commodity is the generic's alpha prefix: CTDEC1 -> CT.
                        cmd = ''.join(ch for ch in generic[:2] if ch.isalpha())
                        if not cmd:
                            continue
                        idx.setdefault(cmd, {}).setdefault(date_s, {})[generic] = px
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


def _bbg_settle(commodity: str, session_date: str, generic: str):
    """Bloomberg px_last for one (commodity, date, generic_code), or None.

    Gated on BBG_SETTLE_COMMODITIES: the history file holds CT generics only,
    so KC/CC/SB must never resolve here -- they stay honestly blank before the
    ICE capture starts rather than silently borrowing a cotton price."""
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

    ICE FIRST, ALWAYS, AND THE GATE IS THE SESSION: a real settle from
    futures_settle_<date>.csv wins and is returned with source='ice'.
    Bloomberg is consulted ONLY for a session that has no settle file at all
    (the pre-2026-04-27 window predating the capture), never merely because
    one contract is missing from a file that exists. A contract absent from a
    captured session is an honest blank -- rolled off the board, or a blank
    Settle field -- and must stay blank rather than swap vendor mid-series.

    Returns None when neither source has it: an honest absence (weekend,
    holiday, today not yet closed, or a commodity with no backfill), never a
    fabricated value. Bloomberg rows carry no OHLC in this file, so open/high/
    low stay None rather than being invented from the settle."""
    cmd = commodity.upper()
    rows = _read_settle_rows(cmd, session_date)
    if ice_code:
        info = ice_to_generic(ice_code, session_date, prefix=cmd,
                              active_months=COMMODITY_MONTHS.get(cmd))
        generic = info.generic_code if info else None
    else:
        generic = front_month_generic(cmd, session_date)
    if not generic:
        return None
    if generic in rows:
        r = rows[generic]
        return {'generic_code': generic, 'ice_code': ice_code or r['ice_code'],
                'settle': r['settle'], 'open': r['open'], 'high': r['high'],
                'low': r['low'], 'date': session_date, 'source': 'ice'}
    # Session gate: Bloomberg fills only sessions ICE never captured. If a
    # settle file exists for this date, ICE owns the answer -- including
    # 'this contract is not on the board', which is a blank, not a gap to
    # paper over with another vendor.
    #
    # Considered and REJECTED 2026-09-02: relaxing this to "fill any contract
    # absent from ICE's file" would put a real front-month price back on the
    # May-June overlay (ICE's capture omits the live N26 there), but it also
    # reopens the vendor swap this gate exists to stop -- a contract whose
    # Settle field is blank/'N/A' is 'absent' by exactly the same test, and
    # would silently switch vendor mid-series on a session ICE did cover.
    # The two guards in test_price_bbg_fallback.py caught the attempt. The
    # incomplete-capture gap is real but belongs upstream in the ICE capture,
    # not in a price-layer exception that cannot tell the cases apart.
    if _ice_has_session(cmd, session_date):
        return None
    px = _bbg_settle(cmd, session_date, generic)
    if px is None:
        return None
    # ice_code stays a string: pre-change this key was never None, and a
    # consumer that dereferences it must not start crashing on backfilled
    # rows. With no explicit contract requested, the resolved generic is the
    # honest identifier for what was priced.
    return {'generic_code': generic, 'ice_code': ice_code or generic,
            'settle': px, 'open': None, 'high': None, 'low': None,
            'date': session_date, 'source': 'bloomberg'}


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
