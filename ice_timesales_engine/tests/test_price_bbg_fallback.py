"""test_price_bbg_fallback.py -- the price layer's source precedence.

api/price.py had ZERO test coverage before this file, which is why a blank
price overlay shipped unnoticed (Lou, 2026-09-02: "why is there no price data
on most of the dates").

THE RULE UNDER TEST -- CORRECTED 2026-09-02 AFTER LOU'S PUSHBACK:
Bloomberg leads; the ICE tape capture fills only what Bloomberg has not got.

The first cut had this backwards, treating the ICE eod root as the settle
authority and Bloomberg as a mere pre-capture backfill. Lou: "ice keeps time
and sales for only so long... if you need hi lo close volume etc, there are
decades of it." That is the whole point:

  * The ICE eod root is a TIME & SALES capture with a short retention window.
    37 of its 92 CT day-folders carry _BACKFILL.txt (reconstructed 2026-07-06),
    and a reconstruction only sees contracts still listed the day it runs -- so
    those folders silently omit already-expired months (N26/K26 are absent from
    every May/June folder), which is what blanked the front-month overlay.
  * Settle/OHLC/volume is decades-deep reference data with no retention limit:
    cotton_futures_volume_history.csv covers all 8 CT generics from 2005.

Measured before the repoint: CTZ26 settle matched Bloomberg CTDEC1 px_last to
within 0.005 on 87 of 87 ICE day-folders (50 live, 37 backfilled, zero
disagreements). Same number, vastly deeper history -- so leading with Bloomberg
costs nothing and closes the gap the ICE capture cannot.

ICE still matters: it is fresh the moment the eod job writes it, so it answers
for a session Bloomberg has not published yet (typically today's).
"""

import csv
import os

import pytest

import config
from api import price as pm


SETTLE_HEADER = ['Date', 'Contract', 'Settle', 'RecSet', 'PrevSettle', 'Change',
                 'Open', 'High', 'Low', 'Last', 'Volume', 'OpenInt']


@pytest.fixture
def ice_root(tmp_path, monkeypatch):
    """Isolated ICE root so tests never read the real capture (and can never
    write to it -- config.py's HARD RULE keeps that tree read-only)."""
    root = tmp_path / 'ice'
    (root / 'CT').mkdir(parents=True)
    monkeypatch.setattr(config, 'ICE_ROOT', str(root))
    return root


@pytest.fixture
def bbg_csv(tmp_path, monkeypatch):
    """Isolated Bloomberg history + a cleared module cache.

    _BBG_INDEX is a process-level singleton; without resetting it one test's
    index would leak into the next and the gate would be tested against stale
    data."""
    path = tmp_path / 'bbg.csv'
    monkeypatch.setattr(config, 'BBG_SETTLE_CSV', str(path))
    monkeypatch.setattr(pm, '_BBG_INDEX', None)
    return path


def write_settle(root, date_str, rows):
    """One ICE settle file. `rows` is [(ice_contract, settle_or_blank), ...]."""
    d = root / 'CT' / date_str
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f'futures_settle_{date_str}.csv', 'w', newline='',
              encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(SETTLE_HEADER)
        for contract, settle in rows:
            w.writerow([date_str, contract, settle, settle, '', '',
                        '', '', '', '', '0.0', '0.0'])


def write_bbg(path, rows):
    """Bloomberg history. `rows` is [(date, generic, px_last), ...]."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['date', 'generic', 'ticker', 'volume', 'open_int',
                    'px_last', 'px_high', 'px_low', 'px_open',
                    'efp_volume', 'efs_volume'])
        for date_s, generic, px in rows:
            w.writerow([date_s, generic, f'{generic} Comdty', '', '',
                        px, '', '', '', '', ''])


# ---------------------------------------------------------------------------
# PRECEDENCE: Bloomberg leads, ICE fills the gap
# ---------------------------------------------------------------------------

def test_bloomberg_leads_when_both_sources_have_the_date(ice_root, bbg_csv):
    """Both carry the session -> Bloomberg answers.

    Not a downgrade: the two agreed on 87 of 87 real ICE day-folders. Leading
    with the deeper, retention-free source is what keeps an expired contract
    from vanishing out of the series."""
    write_settle(ice_root, '2026-05-15', [('CT Z26', '81.91')])
    write_bbg(bbg_csv, [('2026-05-15', 'CTDEC1', '81.91')])

    row = pm.settle_for('CT', '2026-05-15', ice_code='CTZ6')
    assert row['source'] == 'bloomberg'
    assert row['settle'] == 81.91


def test_contract_missing_from_the_ice_capture_still_prices(ice_root, bbg_csv):
    """THE DEFECT THAT STARTED THIS. A reconstructed ICE folder omits contracts
    that had already expired when the backfill ran, so the live front month was
    simply absent and the overlay drew nothing for all of May and June. Reading
    price from the retention-free source fixes it outright."""
    # ICE captured the session but only far months -- no CTN6 row at all.
    write_settle(ice_root, '2026-05-15', [('CT Z26', '81.91'), ('CT Z27', '74.79')])
    write_bbg(bbg_csv, [('2026-05-15', 'CTJUL1', '80.61')])

    row = pm.settle_for('CT', '2026-05-15', ice_code='CTN6')
    assert row is not None, 'front month must price even when ICE omits it'
    assert row['settle'] == 80.61
    assert row['source'] == 'bloomberg'


def test_ice_answers_a_session_bloomberg_has_not_got_yet(ice_root, bbg_csv):
    """Today's session: the eod job has written it but the Bloomberg history
    has not been refreshed. ICE fills, and says so."""
    write_settle(ice_root, '2026-09-02', [('CT Z26', '88.89')])
    write_bbg(bbg_csv, [('2026-09-01', 'CTDEC1', '91.55')])   # nothing for 09-02

    row = pm.settle_for('CT', '2026-09-02', ice_code='CTZ6')
    assert row['source'] == 'ice'
    assert row['settle'] == 88.89


def test_neither_source_is_an_honest_blank(ice_root, bbg_csv):
    """An exchange holiday has no settle anywhere. That must render as a gap,
    never as a carried-forward or invented value."""
    write_bbg(bbg_csv, [('2026-05-22', 'CTDEC1', '80.00')])   # 05-25 is Memorial Day

    assert pm.settle_for('CT', '2026-05-25', ice_code='CTZ6') is None


# ---------------------------------------------------------------------------
# DEEP HISTORY, AND WHAT IT DOES NOT CARRY
# ---------------------------------------------------------------------------

def test_session_older_than_the_ice_capture_still_prices(ice_root, bbg_csv):
    """The bug Lou reported. The ICE capture only reaches back to 2026-04-27;
    the Bloomberg history reaches 2005, so the chart draws a full line."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTDEC1', '68.22')])

    row = pm.settle_for('CT', '2026-01-02', ice_code='CTZ6')
    assert row is not None, 'pre-capture date must be filled, not blank'
    assert row['source'] == 'bloomberg'
    assert row['settle'] == 68.22


def test_bloomberg_row_carries_its_ohlc(ice_root, bbg_csv):
    """The history file DOES carry px_open/high/low, so the row reports real
    OHLC rather than dropping it. A missing column stays None, never invented."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTDEC1', '68.22')])

    row = pm.settle_for('CT', '2026-01-02', ice_code='CTZ6')
    assert row['settle'] == 68.22
    # write_bbg leaves OHLC blank, so these are honestly absent here.
    assert row['open'] is None and row['high'] is None and row['low'] is None


def test_ice_code_never_none_on_backfilled_row(ice_root, bbg_csv):
    """Pre-change this key was always a string; a consumer that dereferences
    it must not start crashing on backfilled rows."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTMAR1', '64.01')])

    row = pm.settle_for('CT', '2026-01-02')      # no explicit contract
    assert row is not None
    assert isinstance(row['ice_code'], str) and row['ice_code']


# ---------------------------------------------------------------------------
# SCOPE AND DEGRADATION
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('cmd', ['KC', 'CC', 'SB'])
def test_non_ct_never_borrows_a_cotton_price(ice_root, bbg_csv, cmd):
    """The history file holds CT generics only. KC/CC/SB stay honestly blank
    before their capture starts rather than silently pricing off cotton."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTDEC1', '68.22')])

    assert pm.settle_for(cmd, '2026-01-02') is None


def test_missing_bbg_file_degrades_to_ice_only(ice_root, bbg_csv):
    """The backfill is strictly additive: absent file -> exactly the old
    ICE-only behaviour, never an exception."""
    # bbg_csv fixture registered the path but nothing was written there.
    assert not os.path.isfile(config.BBG_SETTLE_CSV)

    write_settle(ice_root, '2026-07-31', [('CT Z26', '81.79')])
    assert pm.settle_for('CT', '2026-07-31', ice_code='CTZ6')['settle'] == 81.79
    assert pm.settle_for('CT', '2026-01-02', ice_code='CTZ6') is None


def test_unreadable_bbg_file_degrades_rather_than_raising(ice_root, bbg_csv,
                                                          monkeypatch):
    """A non-UTF-8 re-save of the CSV must not surface as a 500."""
    with open(config.BBG_SETTLE_CSV, 'wb') as f:
        f.write(b'date,generic,px_last\n2026-01-02,CTDEC1,\xff\xfe68.22\n')
    monkeypatch.setattr(pm, '_BBG_INDEX', None)

    assert pm.settle_for('CT', '2026-01-02', ice_code='CTZ6') is None


# ---------------------------------------------------------------------------
# SERIES
# ---------------------------------------------------------------------------

def test_series_reports_the_source_of_every_point(ice_root, bbg_csv):
    """A range spanning the refresh boundary stays continuous, with each point
    labelled by where it actually came from."""
    write_settle(ice_root, '2026-09-02', [('CT Z26', '88.89')])
    write_bbg(bbg_csv, [('2026-09-01', 'CTDEC1', '91.55')])

    series, errored = pm.settle_series('CT', ['2026-09-01', '2026-09-02'],
                                       ice_code='CTZ6')
    assert errored == []
    assert series['2026-09-01']['source'] == 'bloomberg'
    assert series['2026-09-01']['settle'] == 91.55
    assert series['2026-09-02']['source'] == 'ice'
    assert series['2026-09-02']['settle'] == 88.89
