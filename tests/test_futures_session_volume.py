"""
tests/test_futures_session_volume.py -- hermetic tests for the futures engine.

No live Bloomberg, no live API, no real seed file: all fixtures are in-memory or
written to a tmp dir. Covers window/share math, universe filtering (Oct/Aug out),
symbology (non-Dec months + 4-digit year), RVOL graceful degradation,
night-share/ratio, holiday guard, idempotent+permanent history, loud-fail-with-path.
"""

from __future__ import annotations

import csv
import os
import re

import pytest

import config
import futures_session_volume as F
from contract_resolver import build_capture_universe


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

AS_OF = '2026-06-17'


@pytest.fixture()
def universe():
    return build_capture_universe(AS_OF)


@pytest.fixture()
def seed_min():
    """Tiny in-memory seed: 3 dates, a couple generics + an EXCLUDED CTOCT1."""
    return {
        '2026-06-15': {'CTDEC1': 30000.0, 'CTMAR1': 8000.0, 'CTOCT1': 999.0},
        '2026-06-16': {'CTDEC1': 34000.0, 'CTMAR1': 10000.0},
        '2026-06-17': {'CTDEC1': 33926.0, 'CTMAR1': 10204.0, 'CTJUL1': 7673.0},
    }


@pytest.fixture()
def tmp_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'HISTORY_DIR', str(tmp_path / 'history'))
    monkeypatch.setattr(config, 'FUT_HISTORY_CSV',
                        str(tmp_path / 'history' / 'fut_hist.csv'))
    monkeypatch.setattr(config, 'FUT_HISTORY_BY_CONTRACT',
                        str(tmp_path / 'history' / 'fut_hist_by_contract.csv'))
    return tmp_path


# ---------------------------------------------------------------------------
# UNIVERSE / SYMBOLOGY  — Oct/Aug excluded, non-Dec resolves, 4-digit year
# ---------------------------------------------------------------------------

def test_universe_is_eight_slots(universe):
    assert set(universe) == {
        'CTDEC1', 'CTDEC2', 'CTMAR1', 'CTMAR2',
        'CTMAY1', 'CTMAY2', 'CTJUL1', 'CTJUL2',
    }


def test_october_generic_excluded(universe):
    assert F._enrich('CTOCT1', AS_OF, universe) is None
    assert F._enrich('CTAUG1', AS_OF, universe) is None


def test_enrich_non_dec_month_and_year(universe):
    info = F._enrich('CTMAR1', AS_OF, universe)
    assert info['ice_code'] == 'CTH7'
    assert info['delivery_year'] == 2027        # 4-digit, decade-safe
    assert info['month_name'] == 'Mar'
    assert info['position'] == 1


def test_full_for_date_drops_october(universe, seed_min):
    fd = F.full_for_date('2026-06-15', universe, seed_min)
    assert 'CTOCT1' not in fd['per_generic']     # excluded everywhere
    assert 'CTDEC1' in fd['per_generic']


# ---------------------------------------------------------------------------
# SIDECAR DIRECT  — night/day/full straight from RTD boundary cumulatives
# ---------------------------------------------------------------------------

def _write_sidecar(path, rows):
    """Helper: write a minimal ct_futures_volume.csv with given boundary rows."""
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['timestamp', 'date', 'commodity', 'contract',
                    'boundary', 'volume', 'oi'])
        for row in rows:
            w.writerow(row)


def test_sidecar_direct_night_day_full(tmp_path, universe):
    """night+day==full exactly; math: open=1000, 0700=1600, 1420=2600."""
    p = tmp_path / 'ct_futures_volume.csv'
    _write_sidecar(p, [
        ['t', '2026-06-17', 'CT', 'CTDEC1', 'open', 1000, 0],
        ['t', '2026-06-17', 'CT', 'CTDEC1', '0700', 1600, 0],
        ['t', '2026-06-17', 'CT', 'CTDEC1', '1420', 2600, 0],
    ])
    wd = F.read_sidecar_direct(str(p), '2026-06-17', universe)
    assert wd is not None
    assert wd['source'] == 'rtd'
    c = wd['per_generic']['CTDEC1']
    assert c['night'] == pytest.approx(600.0)   # 1600 - 1000
    assert c['day']   == pytest.approx(1000.0)  # 2600 - 1600
    assert c['full']  == pytest.approx(1600.0)  # night + day
    assert c['night'] + c['day'] == pytest.approx(c['full'])


def test_sidecar_direct_drops_october(tmp_path, universe):
    """CTOCT1 in sidecar must be excluded even if all three boundaries present."""
    p = tmp_path / 'ct_futures_volume.csv'
    _write_sidecar(p, [
        ['t', '2026-06-17', 'CT', 'CTOCT1', 'open', 1000, 0],
        ['t', '2026-06-17', 'CT', 'CTOCT1', '0700', 1600, 0],
        ['t', '2026-06-17', 'CT', 'CTOCT1', '1420', 2600, 0],
    ])
    wd = F.read_sidecar_direct(str(p), '2026-06-17', universe)
    assert wd is None   # no in-universe contracts -> None


def test_sidecar_direct_partial_boundary_skipped(tmp_path, universe):
    """Contract missing 1420 boundary is skipped; others still processed."""
    p = tmp_path / 'ct_futures_volume.csv'
    _write_sidecar(p, [
        # CTDEC1: complete
        ['t', '2026-06-17', 'CT', 'CTDEC1', 'open', 1000, 0],
        ['t', '2026-06-17', 'CT', 'CTDEC1', '0700', 1600, 0],
        ['t', '2026-06-17', 'CT', 'CTDEC1', '1420', 2600, 0],
        # CTMAR1: missing 1420 -> skipped
        ['t', '2026-06-17', 'CT', 'CTMAR1', 'open', 500, 0],
        ['t', '2026-06-17', 'CT', 'CTMAR1', '0700', 700, 0],
    ])
    wd = F.read_sidecar_direct(str(p), '2026-06-17', universe)
    assert 'CTDEC1' in wd['per_generic']
    assert 'CTMAR1' not in wd['per_generic']


def test_no_sidecar_returns_none(universe):
    assert F.read_sidecar_direct('/no/such/sidecar.csv', '2026-06-17', universe) is None


# ---------------------------------------------------------------------------
# SCHEMA GUARD  — fail loud on column drift, tolerate additive extra columns
# ---------------------------------------------------------------------------

def _write_sidecar_custom(path, header, rows):
    """Helper: write a sidecar with an ARBITRARY header (for schema-drift tests)."""
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def test_sidecar_missing_required_column_raises(tmp_path, universe):
    """A sidecar missing a required column (volume) must RAISE loud, naming the
    missing column / path — never silently mis-read producer schema drift."""
    p = tmp_path / 'ct_futures_volume.csv'
    # Drop 'volume' from the contract.
    _write_sidecar_custom(p,
        ['timestamp', 'date', 'commodity', 'contract', 'boundary', 'oi'],
        [
            ['t', '2026-06-17', 'CT', 'CTDEC1', 'open', 0],
            ['t', '2026-06-17', 'CT', 'CTDEC1', '0700', 0],
            ['t', '2026-06-17', 'CT', 'CTDEC1', '1420', 0],
        ])
    with pytest.raises(ValueError) as exc:
        F.read_sidecar_direct(str(p), '2026-06-17', universe)
    msg = str(exc.value)
    assert 'volume' in msg            # names the missing column
    assert os.path.abspath(str(p)) in msg   # names the offending path


def test_sidecar_extra_column_tolerated(tmp_path, universe):
    """All 7 required columns PLUS an extra unexpected column must still WORK —
    additive producer changes are allowed by the contract."""
    p = tmp_path / 'ct_futures_volume.csv'
    _write_sidecar_custom(p,
        ['timestamp', 'date', 'commodity', 'contract', 'boundary',
         'volume', 'oi', 'new_extra_field'],
        [
            ['t', '2026-06-17', 'CT', 'CTDEC1', 'open', 1000, 0, 'x'],
            ['t', '2026-06-17', 'CT', 'CTDEC1', '0700', 1600, 0, 'y'],
            ['t', '2026-06-17', 'CT', 'CTDEC1', '1420', 2600, 0, 'z'],
        ])
    wd = F.read_sidecar_direct(str(p), '2026-06-17', universe)   # must not raise
    assert wd is not None
    c = wd['per_generic']['CTDEC1']
    assert c['full'] == pytest.approx(1600.0)


# ---------------------------------------------------------------------------
# RVOL  — graceful degradation, HIGH/LOW flags
# ---------------------------------------------------------------------------

def test_rvol_full_tiers():
    rv = F.compute_rvol(100.0, [50.0] * 60)
    assert rv['5']['rvol'] == pytest.approx(2.0)
    assert rv['60']['n'] == 60
    assert rv['60']['note'] is None


def test_rvol_degrades_when_short():
    rv = F.compute_rvol(100.0, [50.0, 50.0, 50.0])   # only 3 priors
    assert rv['5']['n'] == 3
    assert 'have 3 of 5' in rv['5']['note']
    assert rv['60']['avg'] is None or rv['60']['n'] == 3


def test_rvol_zero_history_is_na():
    rv = F.compute_rvol(100.0, [])
    assert all(rv[str(t)]['rvol'] is None for t in config.LOOKBACK_TIERS)


def test_rvol_none_current_is_na():
    rv = F.compute_rvol(None, [50.0] * 10)
    assert rv['5']['rvol'] is None


# ---------------------------------------------------------------------------
# RATIOS  — night_share / night_day_ratio
# ---------------------------------------------------------------------------

def test_ratios():
    summary = {'night': 30.0, 'day': 70.0, 'full': 100.0}
    r = F._ratios(summary)
    assert r['night_share'] == pytest.approx(0.30)
    assert r['night_day_ratio'] == pytest.approx(30 / 70)


def test_ratios_none_when_no_split():
    r = F._ratios({'night': None, 'day': None, 'full': 100.0})
    assert r['night_share'] is None and r['night_day_ratio'] is None


# ---------------------------------------------------------------------------
# FULL HISTORY  — aggregate trailing, newest first, graceful at boundary
# ---------------------------------------------------------------------------

def test_full_total_history_newest_first(seed_min):
    h = F.full_total_history('CT', '2026-06-17', seed_min)
    # before 06-17: 06-16 (34000+10000=44000), 06-15 (30000+8000=38000; Oct dropped? no,
    # full_total_history sums raw seed values incl CTOCT since it sums seed[d].values())
    assert h[0] == pytest.approx(44000.0)   # 06-16 newest


# ---------------------------------------------------------------------------
# HOLIDAY GUARD
# ---------------------------------------------------------------------------

def test_holiday_guard_clean_exit(seed_min, tmp_history):
    # 2026-05-25 is in CT_CLOSED_DATES
    rc = F.process_session('CT', '2026-05-25', no_write=False, seed=seed_min)
    assert rc == 0
    assert not os.path.isfile(config.FUT_HISTORY_CSV)   # nothing written


# ---------------------------------------------------------------------------
# LOUD FAIL  — missing seed raises with the absolute path
# ---------------------------------------------------------------------------

def test_loud_fail_missing_seed(tmp_path):
    missing = str(tmp_path / 'no_seed.csv')
    with pytest.raises(FileNotFoundError, match=re.escape(missing)):
        F.load_seed(missing)


# ---------------------------------------------------------------------------
# PERMANENT, IDEMPOTENT HISTORY
# ---------------------------------------------------------------------------

def test_history_written_and_idempotent(seed_min, tmp_history):
    rc1 = F.process_session('CT', '2026-06-17', no_write=False, seed=seed_min)
    assert rc1 == 0
    with open(config.FUT_HISTORY_CSV, newline='', encoding='utf-8') as fh:
        rows1 = list(csv.DictReader(fh))
    n1 = len(rows1)
    # Re-run same date: must REPLACE, not duplicate.
    F.process_session('CT', '2026-06-17', no_write=False, seed=seed_min)
    with open(config.FUT_HISTORY_CSV, newline='', encoding='utf-8') as fh:
        rows2 = list(csv.DictReader(fh))
    assert len(rows2) == n1
    dates = [r['date'] for r in rows2]
    assert dates.count('2026-06-17') == 1


def test_history_permanent_keeps_other_dates(seed_min, tmp_history):
    F.process_session('CT', '2026-06-16', no_write=False, seed=seed_min)
    F.process_session('CT', '2026-06-17', no_write=False, seed=seed_min)
    with open(config.FUT_HISTORY_CSV, newline='', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    dates = {r['date'] for r in rows}
    assert {'2026-06-16', '2026-06-17'} <= dates   # earlier date not purged


def test_by_contract_history_carries_symbology(seed_min, tmp_history):
    F.process_session('CT', '2026-06-17', no_write=False, seed=seed_min)
    with open(config.FUT_HISTORY_BY_CONTRACT, newline='', encoding='utf-8') as fh:
        rows = {r['generic_code']: r for r in csv.DictReader(fh)}
    assert rows['CTDEC1']['ice_code'] == 'CTZ6'
    assert rows['CTDEC1']['delivery_year'] == '2026'
    assert rows['CTMAR1']['ice_code'] == 'CTH7'


def test_sidecar_direct_accepts_ice_codes(tmp_path, universe):
    """Regression: the LIVE price_tape sidecar uses ICE codes (CTZ6), not
    generics (CTDEC1). read_sidecar_direct must normalise ICE->generic via
    ice_to_generic; October (CTV) must stay excluded. (This is the integration
    gap that kept every history row source=bbg_seed.)"""
    p = tmp_path / 'ct_futures_volume.csv'
    _write_sidecar(p, [
        ['t', '2026-06-17', 'CT', 'CTZ6', 'open', 100, 0],
        ['t', '2026-06-17', 'CT', 'CTZ6', '0700', 700, 0],
        ['t', '2026-06-17', 'CT', 'CTZ6', '1420', 900, 0],
        ['t', '2026-06-17', 'CT', 'CTV6', 'open', 0, 0],    # October -> excluded
        ['t', '2026-06-17', 'CT', 'CTV6', '0700', 50, 0],
        ['t', '2026-06-17', 'CT', 'CTV6', '1420', 60, 0],
    ])
    wd = F.read_sidecar_direct(str(p), '2026-06-17', universe)
    assert wd is not None
    assert 'CTDEC1' in wd['per_generic']            # ICE CTZ6 -> generic CTDEC1
    c = wd['per_generic']['CTDEC1']
    assert c['ice_code'] == 'CTZ6'
    assert c['night'] == pytest.approx(600.0)       # 700 - 100
    assert c['day']   == pytest.approx(200.0)       # 900 - 700
    assert c['full']  == pytest.approx(800.0)
    assert 'CTOCT1' not in wd['per_generic']         # October never appears
    assert 'CTV6'   not in wd['per_generic']
