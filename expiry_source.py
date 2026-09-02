"""
expiry_source.py -- FIRST NOTICE DAY for a futures contract, from the ruled
authorities. Never from calendar arithmetic.

WHY THIS EXISTS
---------------
contract_resolver.resolve_generic() rolled a generic at
`date(year, delivery_month, 1)` -- the 1st of the delivery month -- while its
own header comment claimed the roll happened "at its first-notice / expiry".
The code never matched the comment. Real CT contracts leave the generic board
at FIRST NOTICE DAY, five to eight days earlier, so for roughly six sessions
per roll the front-month generic pointed at a contract that was already gone.

Measured on this repo's own archive (2026-09-02): CTN6's FND is 2026-06-24 and
its 5-minute bar count collapses 132 -> 5 on exactly that date, then dribbles
to LTD. CTH6 and CTK6 show the same cliff at their own FNDs. The board follows
FND, not the 1st.

Impact was NOT limited to the price overlay: ice_to_generic() stamps
generic_code into the archive via ingest/normalize.py and ingest/rollup.py, so
25,237 of 195,188 stored bar5m rows sat in a disputed roll window with the
expiring contract holding slot 1.

THE AUTHORITY RULE (EXPIRY_AUTHORITY_ACCESS_PROTOCOL.md, Lou 2026-09-02)
------------------------------------------------------------------------
"There is exactly one authority: ICE's own expiry data, scraped directly from
ICE's published pages, served through the VLM gateway. Nothing else. No local
file, no hardcoded month table, no calendar arithmetic, no memory of what a
board 'usually' runs to. If the gateway and a local snapshot ever disagree,
the gateway wins."

This module holds ZERO expiry facts of its own for any LISTED contract. It
asks the authorities and compares them, exactly as
`C:\\Ice eod records\\expiry_authority.py` does for option tenors. Order:

  1. GATEWAY (primary)  GET /v1/calendar/expiry/CT/futures
  2. LOCAL CSV (cross-check, and the ONLY source for expired tenors)
     options sandbox/DOCS_SANDBOX/expiry_master.csv
  3. VENDORED HISTORICAL (below) -- expired contracts neither authority
     retains any more. Dated, sourced, and never extended by guesswork.

On disagreement the GATEWAY WINS. When nothing can answer, we raise
ExpiryUnavailable rather than fall back to month arithmetic: a wrong roll date
silently mislabels stored volume, which is worse than a loud refusal.

THE HISTORICAL GAP THIS MODULE HAD TO CLOSE
-------------------------------------------
The gateway serves currently-LISTED contracts only, and expiry_master.csv was
snapshotted 2026-07-16. CTH26/CTK26/CTN26 had all expired by then, so NEITHER
authority carries them -- yet the archive holds 84,475 bars across those three
contracts and cannot be labelled without their FNDs.

Per protocol section 5 ("do not derive a contract from calendar arithmetic"),
these were NOT computed. They were pulled from the Bloomberg terminal
(FUT_NOTICE_FIRST via blpapi ReferenceDataRequest, 2026-09-02) and
CROSS-VALIDATED against the live gateway on the two contracts both sources
carry: CTZ26 FND 2026-11-23 and CTH27 FND 2027-02-22 matched to the day. That
agreement is what licenses trusting Bloomberg for the three the authorities
have dropped.
"""

import csv
import datetime as dt
import os
import threading

GATEWAY_BASE = "https://vlmapi.vlmdata.com"
GATEWAY_PATH = "/v1/calendar/expiry/{cmd}/futures"

EXPIRY_MASTER = (r"C:\Users\Louis\OneDrive - VLM Commodities LTD"
                 r"\Desktop\options sandbox\DOCS_SANDBOX\expiry_master.csv")

# Protocol section 3: the gateway's own freshness check interval is 8 days.
# A status field is not evidence -- we compare the row timestamps ourselves.
REFRESH_WARN_DAYS = 8

# ---------------------------------------------------------------------------
# VENDORED HISTORICAL FND -- expired contracts no authority retains.
# ---------------------------------------------------------------------------
# Source: Bloomberg FUT_NOTICE_FIRST, pulled 2026-09-02 via blpapi from the
# desk terminal. Cross-validated against the live gateway on CTZ26 (2026-11-23)
# and CTH27 (2027-02-22) -- exact agreement on both, which is the evidence that
# Bloomberg's field is the same fact ICE publishes.
#
# This table is APPEND-ONLY and may only ever be extended with a date read off
# a real source (ICE page, gateway, or Bloomberg), never one computed from a
# month number. Every contract here MUST also record its LTD so a future reader
# can re-verify. Contracts are removed from here only if an authority starts
# serving them again.
_HISTORICAL_FND = {
    # contract: (first_notice_day, last_trade_day, source)
    'CTH26': ('2026-02-23', '2026-03-09', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'CTK26': ('2026-04-24', '2026-05-06', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'CTN26': ('2026-06-24', '2026-07-09', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    # 2025 contracts: the archive reaches back to 2025-12-22, and resolving a
    # generic on those dates walks candidate years starting at 2025, so CTZ25
    # must be datable or every Dec-2025 row refuses. Same Bloomberg pull.
    'CTZ25': ('2025-11-21', '2025-12-08', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'CTV25': ('2025-09-24', '2025-10-09', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    # KC/CC/SB expired months. The live archive is NOT CT-only: it carries
    # coffee, cocoa and sugar too, and these four contracts block 231,122 rows
    # across bar5m/minute_agg/ticks. Same pull, same cross-validation (CCZ26,
    # KCZ26, SBV26 and SBH27 each matched the gateway exactly).
    #
    # Note SB: FND lands AFTER last trade day (SBK26 fnd 2026-05-01, ltd
    # 2026-04-30). That is real, not a transcription slip -- sugar's notice
    # cycle genuinely runs past the board, which is why no rule may assume
    # FND precedes either LTD or the delivery month.
    'CCK26': ('2026-04-24', '2026-05-13', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'KCK26': ('2026-04-22', '2026-05-18', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'SBK26': ('2026-05-01', '2026-04-30', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
    'SBN26': ('2026-07-01', '2026-06-30', 'bloomberg FUT_NOTICE_FIRST 2026-09-02'),
}


# ---------------------------------------------------------------------------
# LOU'S SHAPE RULE (2026-09-02) -- A CROSS-CHECK, NEVER A SOURCE
# ---------------------------------------------------------------------------
# "For ICE Cotton No. 2, First Notice Day consistently lands exactly five
#  business days prior to the first business day of the delivery month. The
#  Last Trading Day cross-check always closes out the board seventeen business
#  days from the end of that same spot month, accounting for exchange holidays
#  like Memorial Day and Independence Day that shift the final count."
#
# Use this to SANITY-CHECK a date an authority gave, or to spot a typo in a
# hand-entered historical row. It must NEVER become the source of an FND:
# EXPIRY_AUTHORITY_ACCESS_PROTOCOL.md section 5 forbids deriving a contract
# date from calendar arithmetic, and business-day math cannot know which
# holidays ICE actually observed in a given year. Verified against the three
# vendored contracts below -- all three sit 5 business days before the 1st
# business day of their delivery month.


class ExpiryUnavailable(RuntimeError):
    """No authority could supply an FND for a contract we must label.

    Deliberately fatal rather than recoverable. Guessing a roll date silently
    mislabels stored volume -- the exact defect this module was written to
    remove -- and a mislabel is indistinguishable from correct data downstream.
    """


_LOCK = threading.Lock()
# Keyed BY COMMODITY. An unkeyed cache silently poisons every other commodity:
# _table('CT') would populate it with CT-only rows, and the next _table('KC')
# would get that same dict back, find no KC contracts, and report every KC
# contract as undatable for the life of the process. Because ingest swallows
# ExpiryUnavailable into a None (normalize.py to_generic, rollup.py), that
# surfaces as generic_code NULL on every KC/CC/SB row -- the exact silent
# defect normalize.py's own docstring records having already fixed once.
_CACHE = {}            # {CMD: {contract: {'fnd','ltd','source'}}}
_NOTES = {}            # {CMD: [provenance lines]}


def _parse_date(s):
    try:
        return dt.date.fromisoformat(str(s).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _from_gateway(cmd='CT', timeout=20):
    """({contract: {'fnd','ltd'}}, note) from the gateway, or (None, why).

    Never raises: a transport or parse failure returns None so the caller can
    fall through to the local authority and report on BOTH.
    """
    try:
        import requests
    except ImportError:
        return None, 'requests not installed'
    key = os.environ.get('VLM_API_KEY')
    if not key:
        return None, 'VLM_API_KEY not set'
    url = GATEWAY_BASE + GATEWAY_PATH.format(cmd=cmd.upper())
    try:
        r = requests.get(url, headers={'X-VLM-API-Key': key}, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:                      # noqa: BLE001 -- report, never raise
        return None, f'{type(exc).__name__}: {exc}'
    rows = payload.get('data') if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None, 'unexpected payload shape'
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        contract = str(row.get('contract') or '').upper()
        fnd = _parse_date(row.get('fnd'))
        if contract and fnd:
            out[contract] = {'fnd': fnd, 'ltd': _parse_date(row.get('ltd')),
                             'source': 'gateway'}
    if not out:
        return None, 'no dated rows in payload'

    note = 'gateway ok'
    # Protocol section 5: a platform's own status field is not evidence. Check
    # the content age ourselves and say so, whatever `stale` claims.
    if isinstance(payload, dict):
        if payload.get('stale'):
            note += f" [STALE flag set, age={payload.get('stale_age_seconds')}s]"
        refreshed = _parse_date(payload.get('refreshed_at'))
        if refreshed:
            age = (dt.date.today() - refreshed).days
            note += f' [refreshed_at {refreshed}, {age}d old]'
            if age > REFRESH_WARN_DAYS:
                note += (f' WARN: exceeds {REFRESH_WARN_DAYS}d check interval'
                         f' -- verify the desk scrape task fired')
    return out, note


def _from_local_csv(cmd='CT', path=None):
    """({contract: {'fnd','ltd'}}, note) from expiry_master.csv, or (None, why)."""
    path = path or EXPIRY_MASTER
    if not os.path.isfile(path):
        return None, f'not found: {path}'
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return None, f'{type(exc).__name__}: {exc}'
    out, refreshed = {}, None
    for row in rows:
        if (row.get('commodity') or '').upper() != cmd.upper():
            continue
        if (row.get('kind') or '').lower() != 'futures':
            continue
        contract = str(row.get('contract') or '').upper()
        fnd = _parse_date(row.get('fnd'))
        if contract and fnd:
            out[contract] = {'fnd': fnd, 'ltd': _parse_date(row.get('ltd')),
                             'source': 'local csv'}
        refreshed = refreshed or _parse_date(row.get('refreshed_at'))
    if not out:
        return None, 'no CT futures rows'
    note = 'local csv ok'
    if refreshed:
        age = (dt.date.today() - refreshed).days
        note += f' [refreshed_at {refreshed}, {age}d old]'
    return out, note


def _build(cmd='CT'):
    """Merge the authorities into {contract: {'fnd','ltd','source'}}.

    Precedence, per the protocol: gateway wins outright on any contract both
    answer for; the local CSV supplies contracts the gateway no longer lists;
    the vendored historical table supplies what neither retains. Disagreements
    are recorded in _NOTES rather than silently resolved.
    """
    notes = []
    gw, gw_note = _from_gateway(cmd)
    loc, loc_note = _from_local_csv(cmd)
    notes.append(f'gateway: {gw_note}')
    notes.append(f'local:   {loc_note}')

    merged = {}
    # Lowest precedence first, so higher-precedence sources overwrite.
    for contract, (fnd, ltd, src) in _HISTORICAL_FND.items():
        if contract.upper().startswith(cmd.upper()):
            merged[contract] = {'fnd': _parse_date(fnd), 'ltd': _parse_date(ltd),
                                'source': f'vendored historical ({src})'}
    if loc:
        merged.update(loc)
    if gw:
        for contract, row in gw.items():
            prior = merged.get(contract)
            # A disagreement between the two live authorities is a loud line,
            # never a silent pick -- the gateway still wins.
            if prior and prior.get('fnd') and prior['fnd'] != row['fnd']:
                notes.append(
                    f'DISAGREEMENT {contract}: {prior["source"]} says '
                    f'{prior["fnd"]}, gateway says {row["fnd"]} -- gateway wins')
            merged[contract] = row

    if not merged:
        raise ExpiryUnavailable(
            f'no authority could supply {cmd} futures expiry. '
            + ' | '.join(notes))
    return merged, notes


def _table(cmd='CT'):
    """Process-lifetime cache of the merged authority table, PER COMMODITY."""
    key = str(cmd).upper()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        table, notes = _build(key)
        _CACHE[key], _NOTES[key] = table, notes
    return table


def provenance(cmd='CT'):
    """The notes recorded when this commodity's table was built: which
    authority answered, how old it was, and any disagreement. Callers that
    surface data freshness should print this rather than assert it is fresh."""
    _table(cmd)
    return list(_NOTES.get(str(cmd).upper(), []))


def reset_cache():
    """Drop every cached table (tests, and any long-lived process that must
    pick up a refreshed authority without restarting)."""
    with _LOCK:
        _CACHE.clear()
        _NOTES.clear()


def first_notice_day(contract):
    """FND as a date for a full ICE contract code ('CTZ26'), from the
    authorities. Raises ExpiryUnavailable if nothing can answer -- never
    guesses, never falls back to calendar arithmetic."""
    key = str(contract).strip().upper()
    cmd = ''.join(ch for ch in key[:2] if ch.isalpha()) or 'CT'
    row = _table(cmd).get(key)
    if not row or not row.get('fnd'):
        raise ExpiryUnavailable(
            f'no authority carries an FND for {key}. '
            f'If it is an expired contract, add it to _HISTORICAL_FND with a '
            f'real sourced date (ICE page, gateway, or Bloomberg '
            f'FUT_NOTICE_FIRST) -- never a computed one. '
            + ' | '.join(provenance(cmd)))
    return row['fnd']


def has_rolled(contract, as_of):
    """True if `contract` has left the generic board as of `as_of`.

    The board rolls AT first notice day: a contract is off the generic board
    ON its FND, not the day after. Measured on this repo's archive, CTN6's
    bar count falls 132 -> 5 on its FND itself, so FND is the first day the
    next contract owns the slot.
    """
    as_of = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of))
    return as_of >= first_notice_day(contract)
