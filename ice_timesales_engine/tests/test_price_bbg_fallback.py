"""test_price_bbg_fallback.py -- the Bloomberg settle backfill and, above all,
the SESSION GATE that keeps it out of ICE's way.

api/price.py had ZERO test coverage before this file, which is why the blank
pre-capture price overlay shipped unnoticed (Lou, 2026-09-02: "why is there no
price data on most of the dates"). The backfill fixes that; these tests pin the
boundary so the fix cannot rot into a silent vendor swap.

THE RULE UNDER TEST, in one line: Bloomberg fills only sessions the ICE capture
never took. A session with a settle file is ICE's to answer -- including
answering "that contract is not on the board", which is a blank, not a gap for
another vendor to paper over.

The near-miss that motivates test_ice_covered_session_never_falls_back: ICE's
futures_settle_<date>.csv is a snapshot of the contracts on the board, while
front_generic() names the nearest calendar month whether or not ICE captured
it. On 2026-05-15 ICE holds Z26/H27/K27/N27/Z27 but the resolver calls CTJUL1
(N26, already rolled off) the front. A per-CONTRACT fallback therefore fired on
every overlap-window date, swapping vendor on days ICE fully covers. The gate is
per-SESSION for exactly that reason.
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
# THE GATE
# ---------------------------------------------------------------------------

def test_ice_covered_session_never_falls_back(ice_root, bbg_csv):
    """A session ICE captured must NEVER return a Bloomberg price -- not even
    for a contract missing from that day's file. This is the whole audit
    finding: the gate is the SESSION, never the contract."""
    # ICE captured this session, but only far months -- no CTZ6 row at all.
    write_settle(ice_root, '2026-05-15', [('CT H27', '82.55'), ('CT Z27', '74.79')])
    # Bloomberg has a tempting price for the very contract ICE lacks.
    write_bbg(bbg_csv, [('2026-05-15', 'CTDEC1', '81.91')])

    assert pm.settle_for('CT', '2026-05-15', ice_code='CTZ6') is None


def test_ice_wins_when_both_sources_have_the_date(ice_root, bbg_csv):
    """Overlap window: ICE's value is returned, Bloomberg's is ignored."""
    write_settle(ice_root, '2026-05-15', [('CT Z26', '81.91')])
    write_bbg(bbg_csv, [('2026-05-15', 'CTDEC1', '99.99')])

    row = pm.settle_for('CT', '2026-05-15', ice_code='CTZ6')
    assert row['source'] == 'ice'
    assert row['settle'] == 81.91


def test_blank_settle_field_stays_blank_not_backfilled(ice_root, bbg_csv):
    """A contract whose Settle is blank/'N/A' is skipped by _read_settle_rows.
    The session is still ICE-covered, so the answer is a blank -- fabricating
    one from Bloomberg would be a silent mid-series vendor swap."""
    write_settle(ice_root, '2026-05-15', [('CT Z26', ''), ('CT H27', '82.55')])
    write_bbg(bbg_csv, [('2026-05-15', 'CTDEC1', '81.91')])

    assert pm.settle_for('CT', '2026-05-15', ice_code='CTZ6') is None


# ---------------------------------------------------------------------------
# THE BACKFILL DOING ITS JOB
# ---------------------------------------------------------------------------

def test_pre_capture_session_fills_from_bloomberg(ice_root, bbg_csv):
    """The bug Lou reported: no ICE folder at all -> Bloomberg fills it."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTDEC1', '68.22')])

    row = pm.settle_for('CT', '2026-01-02', ice_code='CTZ6')
    assert row is not None, 'pre-capture date must be filled, not blank'
    assert row['source'] == 'bloomberg'
    assert row['settle'] == 68.22


def test_bloomberg_row_reports_no_ohlc(ice_root, bbg_csv):
    """The history file carries no OHLC. Those stay None rather than being
    invented from the settle."""
    write_bbg(bbg_csv, [('2026-01-02', 'CTDEC1', '68.22')])

    row = pm.settle_for('CT', '2026-01-02', ice_code='CTZ6')
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

def test_series_mixes_sources_across_the_capture_boundary(ice_root, bbg_csv):
    """A range spanning the boundary is continuous: Bloomberg before, ICE
    after, each point labelled with the source it actually came from."""
    write_settle(ice_root, '2026-04-27', [('CT Z26', '80.97')])
    write_bbg(bbg_csv, [('2026-04-24', 'CTDEC1', '80.58'),
                        ('2026-04-27', 'CTDEC1', '99.99')])   # must lose to ICE

    series, errored = pm.settle_series('CT', ['2026-04-24', '2026-04-27'],
                                       ice_code='CTZ6')
    assert errored == []
    assert series['2026-04-24']['source'] == 'bloomberg'
    assert series['2026-04-24']['settle'] == 80.58
    assert series['2026-04-27']['source'] == 'ice'
    assert series['2026-04-27']['settle'] == 80.97
