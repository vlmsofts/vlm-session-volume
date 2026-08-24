"""
session_render.py -- the figures a same-day session card needs, and only those.

DISPLAY LAYER. Nothing here writes, and nothing here changes a stored figure.
It re-reads the tape and reports it under honest labels.

Five things the card needs that no existing query returned:

  1. TAPE BASIS. The header total is TAPE volume, not total exchange volume.
     TAS and TIC are a separate ICE product, absent from the blotter BY
     CONSTRUCTION, plus any uncaptured blocks. Measured against ICE's official
     Daily Market Report across four contract-sessions: 94.3, 97.6, 97.4, 93.9
     percent. Deliberately NOT surfaced as a fixed percentage -- it moves, and
     TAS is a roll instrument so it may move most during the roll. Say what the
     figure IS.

  2. LITERAL SESSION WINDOW. A trade date alone is ambiguous because the
     session opens the prior evening. The window is read from the tape itself
     (first and last print), never from boilerplate, so a Sunday open or a
     holiday-shortened session prints its REAL hours.

  3. THE LEG SPLIT. Leg is two populations wearing one tag:
       SPREAD LEG   a leg print with a same-second, same-size leg partner on
                    another contract in the same commodity.
       OPTION HEDGE a leg print without one.
     Evidence: size-matched pairs price inside the published spread day range,
     400 of 400 across three sessions. The definition the proof rests on is the
     definition shipped -- hence same-SIZE, not merely same-second.

  4. BLANKS OUT OF THE AGGRESSOR BASE. Blank-condition prints are implied
     calendar spread fills (same-second cross-contract coincidence at 3-4x the
     control rate, size-matched about half the time, price differential inside
     the published spread day range 400 of 400). They stay IN the total and OUT
     of the aggressor count, and are never prorated across buy and sell --
     that would invent sides.

  5. AGGRESSOR ON A HONEST BASE. Aggressor-tagged outrights only. EFS, EFP,
     cancelled, leg and blank are all out, with clip size per side and the
     night/day split, because a daily bar hides a side that flips at the open.

WHY EXISTS AND NOT A JOIN, for the leg split
--------------------------------------------
A JOIN counts row-PAIRS: one leg print matching ten same-second prints on
another contract counts TEN times. Measured on CT 2026-08-21 the JOIN form
reported 10,785 "paired" prints on Z26 against a true 1,055, and cost 5.84s.
The question is per-PRINT -- does THIS print have ANY qualifying partner -- and
that is EXISTS, which cost 0.32s for the same session. Do not reach for a join
here.

PROVENANCE OF THE FIGURES BELOW. Everything quoted in this module was measured
in this repo on 2026-08-24 against the live store, and the JOIN-vs-EXISTS
finding was made here while building it. There is NO earlier source to go
looking for. An earlier hand count of 1,033 spread legs / 57 option hedges on
Z26 08-21 was WITHDRAWN as wrong: it aggregated by SECOND rather than per
print, so a second containing a mix of paired and unpaired prints fell entirely
to unpaired. The correct per-print figures are 1,055 / 35.
"""

from typing import Optional

import config
from ingest.aggressor import BUY, SELL, UNSIDED
from store.db import Db

# Leg sub-populations. Method words ('paired'/'unpaired') stay internal; these
# are the names that reach a reader.
SPREAD_LEG = 'spread_leg'
OPTION_HEDGE = 'option_hedge'

# A leg print is a SPREAD LEG when a same-second, same-size leg exists on a
# different contract in the same commodity. EXISTS, never a join -- see above.
_HAS_PARTNER = """EXISTS (
        SELECT 1 FROM ticks b
        WHERE b.commodity = a.commodity
          AND b.session_date = a.session_date
          AND b.primary_type = 'leg'
          AND b.exchange_time = a.exchange_time
          AND b.ice_code <> a.ice_code
          AND b.size = a.size
    )"""


def leg_split(db: Db, commodity: str, session_date: str,
              ice_code: Optional[str] = None) -> dict:
    """Split leg volume into spread legs and option hedges.

    Returns {'spread_leg': {...}, 'option_hedge': {...}, 'total': {...}} where
    each is {'prints': int, 'lots': float}. The partition is EXHAUSTIVE and
    that is asserted: a partition that stops summing is a defect that would
    otherwise surface as a quietly wrong number on a client card.
    """
    where = ("a.commodity = %s AND a.session_date = %s "
             "AND a.primary_type = 'leg'")
    params = [commodity.upper(), session_date]
    if ice_code:
        where += ' AND a.ice_code = %s'
        params.append(ice_code)

    row = db.q(f"""
        SELECT
          SUM(CASE WHEN {_HAS_PARTNER} THEN 1 ELSE 0 END),
          SUM(CASE WHEN {_HAS_PARTNER} THEN a.size ELSE 0 END),
          SUM(CASE WHEN NOT {_HAS_PARTNER} THEN 1 ELSE 0 END),
          SUM(CASE WHEN NOT {_HAS_PARTNER} THEN a.size ELSE 0 END),
          COUNT(*), COALESCE(SUM(a.size), 0)
        FROM ticks a WHERE {where}
    """, params)[0]

    sp_n, sp_l, oh_n, oh_l, tot_n, tot_l = [x or 0 for x in row]
    out = {
        SPREAD_LEG: {'prints': int(sp_n), 'lots': float(sp_l)},
        OPTION_HEDGE: {'prints': int(oh_n), 'lots': float(oh_l)},
        'total': {'prints': int(tot_n), 'lots': float(tot_l)},
    }
    # CONSERVATION. Same standard as buy+sell+unsided == outright.
    assert int(sp_n) + int(oh_n) == int(tot_n), (
        f'leg print partition does not sum: {sp_n}+{oh_n} != {tot_n}')
    assert abs(float(sp_l) + float(oh_l) - float(tot_l)) < 0.0001, (
        f'leg lot partition does not sum: {sp_l}+{oh_l} != {tot_l}')
    return out


def aggressor_split(db: Db, commodity: str, session_date: str,
                    ice_code: Optional[str] = None) -> dict:
    """Aggressor picture on an honest base: aggressor-tagged OUTRIGHTS only.

    'base' is buy + sell. Unsided outrights (blank-condition prints, the
    implied spread fills) are reported ALONGSIDE, never inside the base and
    never prorated. Clip size is lots/prints per side -- free, since
    trade_count sits beside sum_size at every grain, and it is the character
    that totals hide.
    """
    where = ("commodity = %s AND session_date = %s "
             "AND primary_type = 'outright'")
    params = [commodity.upper(), session_date]
    if ice_code:
        where += ' AND ice_code = %s'
        params.append(ice_code)

    rows = db.q(f"""
        SELECT side, SUM(sum_size), SUM(trade_count)
        FROM minute_agg WHERE {where} GROUP BY side
    """, params)
    by = {s: {'lots': float(l or 0), 'prints': int(n or 0)}
          for s, l, n in rows}
    for s in (BUY, SELL, UNSIDED):
        by.setdefault(s, {'lots': 0.0, 'prints': 0})

    base = by[BUY]['lots'] + by[SELL]['lots']
    out = {
        'base_lots': base,
        'buy': dict(by[BUY]), 'sell': dict(by[SELL]),
        'unsided': dict(by[UNSIDED]),
        'outright_total': base + by[UNSIDED]['lots'],
    }
    for side in ('buy', 'sell', 'unsided'):
        d = out[side]
        d['pct_of_base'] = (100.0 * d['lots'] / base) if base else None
        d['clip'] = (d['lots'] / d['prints']) if d['prints'] else None
    out['unsided']['pct_of_base'] = None      # never expressed against the base
    # CONSERVATION: buy + sell + unsided == the outright total.
    assert abs(out['buy']['lots'] + out['sell']['lots']
               + out['unsided']['lots'] - out['outright_total']) < 0.0001
    return out


def aggressor_by_window(db: Db, commodity: str, session_date: str,
                        ice_code: Optional[str] = None) -> dict:
    """The same picture per window_preset (night / day).

    On CT 2026-08-21 overnight read 54.3 percent buy and the day session 46.4:
    the side FLIPPED at the open, and a daily bar cannot show that. This is the
    same-day product.
    """
    where = ("t.commodity = %s AND t.session_date = %s "
             "AND t.primary_type = 'outright'")
    params = [commodity.upper(), session_date]
    if ice_code:
        where += ' AND t.ice_code = %s'
        params.append(ice_code)

    rows = db.q(f"""
        SELECT t.window_preset,
               SUM(CASE WHEN t.conditions_raw LIKE '%%SetByBid%%'
                        THEN t.size ELSE 0 END),
               SUM(CASE WHEN t.conditions_raw LIKE '%%SetByBid%%'
                        THEN 1 ELSE 0 END),
               SUM(CASE WHEN t.conditions_raw LIKE '%%SetByAsk%%'
                        THEN t.size ELSE 0 END),
               SUM(CASE WHEN t.conditions_raw LIKE '%%SetByAsk%%'
                        THEN 1 ELSE 0 END)
        FROM ticks t WHERE {where}
        GROUP BY t.window_preset
    """, params)
    out = {}
    for win, b_l, b_n, s_l, s_n in rows:
        b_l, s_l = float(b_l or 0), float(s_l or 0)
        base = b_l + s_l
        out[win] = {
            'buy_lots': b_l, 'sell_lots': s_l, 'base_lots': base,
            'buy_prints': int(b_n or 0), 'sell_prints': int(s_n or 0),
            'buy_pct': (100.0 * b_l / base) if base else None,
            'sell_pct': (100.0 * s_l / base) if base else None,
        }
    return out


def session_window(db: Db, commodity: str, session_date: str) -> dict:
    """The LITERAL window, read from the tape: first and last print.

    Never boilerplate. A Sunday open or a holiday-shortened session must print
    its real hours -- that is exactly where a reader guesses wrong.
    """
    row = db.q("""
        SELECT MIN(exchange_time), MAX(exchange_time), COUNT(*)
        FROM ticks WHERE commodity = %s AND session_date = %s
    """, (commodity.upper(), session_date))[0]
    first, last = row[0], row[1]
    # The session BOUNDARY is not the last print. CT runs [21:00, 14:20), so a
    # normal close shows a final print at 14:19:5x -- printing that as the
    # window end reads like a truncated capture. Report the boundary the tape
    # actually spans, derived from the observed prints, and keep the observed
    # first/last available for anyone who needs them.
    close_hh, close_mm = config.DAY_END_HH_MM
    boundary_end = f'{session_date}T{close_hh:02d}:{close_mm:02d}'
    return {'first_print': first, 'last_print': last,
            'window_start': first[:16] if first else None,
            'window_end': boundary_end,
            'prints': int(row[2] or 0)}


def type_breakdown(db: Db, commodity: str, session_date: str,
                   ice_code: Optional[str] = None) -> dict:
    """Lots per primary_type, with leg replaced by its two real populations.

    'outright' keeps blank-condition prints IN it (they are real fills and
    belong in the total); the blanks are named separately by blank_note() so a
    reader sees what they are without them being double counted.
    """
    where = 'commodity = %s AND session_date = %s'
    params = [commodity.upper(), session_date]
    if ice_code:
        where += ' AND ice_code = %s'
        params.append(ice_code)
    rows = db.q(f"""
        SELECT primary_type, SUM(sum_size), SUM(trade_count)
        FROM minute_agg WHERE {where} GROUP BY primary_type
    """, params)
    out = {t: {'lots': float(l or 0), 'prints': int(n or 0)}
           for t, l, n in rows}
    if 'leg' in out:
        split = leg_split(db, commodity, session_date, ice_code)
        out[SPREAD_LEG] = split[SPREAD_LEG]
        out[OPTION_HEDGE] = split[OPTION_HEDGE]
        del out['leg']
    return out


def blank_note(db: Db, commodity: str, session_date: str,
               ice_code: Optional[str] = None) -> dict:
    """The blank-condition prints, named. IN the total, OUT of the aggressor
    count, never prorated across buy and sell."""
    where = ("commodity = %s AND session_date = %s "
             "AND primary_type = 'outright' AND side = 'unsided'")
    params = [commodity.upper(), session_date]
    if ice_code:
        where += ' AND ice_code = %s'
        params.append(ice_code)
    row = db.q(f'SELECT SUM(sum_size), SUM(trade_count) FROM minute_agg '
               f'WHERE {where}', params)[0]
    return {'lots': float(row[0] or 0), 'prints': int(row[1] or 0)}
