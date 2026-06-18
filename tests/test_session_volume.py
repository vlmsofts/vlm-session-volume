"""
tests/test_session_volume.py — Hermetic unit tests for vlm_session_volume.

All tests use in-memory fixtures; no real tape files are read.
Run from vlm_session_volume/ with:  python -m pytest tests/ -v
"""

import csv
import io
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, mock_open

import pytest

# Make the repo root importable regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from contract_resolver import (
    resolve_generic,
    ice_to_generic,
    parse_ice_code,
    futures_prefix_for,
    build_capture_universe,
)
from session_volume import (
    _float,
    _window_cutoff,
    _summarise,
    compute_rvol,
    write_history,
    _prior_sessions,
    extract_overnight,
    extract_day,
    process_session,
    _available_sessions,
)


# ===========================================================================
# HELPERS
# ===========================================================================

def _make_tape_csv(rows: list[dict]) -> str:
    """Serialise a list of row dicts to CSV text."""
    if not rows:
        return 'timestamp,date,commodity,contract,strike,call_vol,put_vol\n'
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _tape_row(ts, date_str, contract, call_vol, put_vol, strike=75.0):
    return {
        'timestamp': ts, 'date': date_str, 'commodity': 'CT',
        'contract': contract, 'strike': strike,
        'call_bid': '', 'call_offer': '', 'call_last': '',
        'call_vol': call_vol, 'call_vol_delta': '',
        'call_block': '', 'call_block_delta': '', 'call_oi': '',
        'put_bid': '', 'put_offer': '', 'put_last': '',
        'put_vol': put_vol, 'put_vol_delta': '',
        'put_block': '', 'put_block_delta': '', 'put_oi': '',
        'futures_last': '', 'futures_mid': '', 'atm_strike': '', 'trade_flag': '',
    }


# ===========================================================================
# 1. contract_resolver — generic resolution
# ===========================================================================

class TestResolveGeneric:
    def test_dec1_jun2026(self):
        info = resolve_generic('CTDEC1', '2026-06-17')
        assert info.ice_code == 'CTZ6'
        assert info.delivery_year == 2026
        assert info.month_code == 'Z'
        assert info.month_name == 'Dec'
        assert info.position == 1

    def test_dec2_jun2026(self):
        info = resolve_generic('CTDEC2', '2026-06-17')
        assert info.ice_code == 'CTZ7'
        assert info.delivery_year == 2027
        assert info.position == 2

    def test_mar1_jun2026(self):
        info = resolve_generic('CTMAR1', '2026-06-17')
        assert info.month_code == 'H'
        assert info.delivery_year == 2027   # Mar 2026 already past
        assert info.position == 1

    def test_mar2_jun2026(self):
        info = resolve_generic('CTMAR2', '2026-06-17')
        assert info.delivery_year == 2028
        assert info.position == 2

    def test_may1_jun2026(self):
        info = resolve_generic('CTMAY1', '2026-06-17')
        assert info.month_code == 'K'
        assert info.delivery_year == 2027   # May 2026 already past
        assert info.position == 1

    def test_jul1_jun2026(self):
        info = resolve_generic('CTJUL1', '2026-06-17')
        assert info.month_code == 'N'
        assert info.delivery_year == 2026   # Jul 2026 not yet started
        assert info.position == 1

    def test_jul2_jun2026(self):
        info = resolve_generic('CTJUL2', '2026-06-17')
        assert info.delivery_year == 2027
        assert info.position == 2

    def test_generic_code_stored(self):
        info = resolve_generic('CTDEC1', '2026-06-17')
        assert info.generic_code == 'CTDEC1'

    def test_all_eight_slots(self):
        universe = build_capture_universe('2026-06-17')
        assert len(universe) == 8
        expected_keys = {
            'CTMAR1', 'CTMAR2', 'CTMAY1', 'CTMAY2',
            'CTJUL1', 'CTJUL2', 'CTDEC1', 'CTDEC2',
        }
        assert set(universe.keys()) == expected_keys

    def test_all_delivery_years_are_4digit(self):
        universe = build_capture_universe('2026-06-17')
        for g, info in universe.items():
            assert info.delivery_year >= 2026, f'{g} has year {info.delivery_year}'
            assert len(str(info.delivery_year)) == 4


class TestIceToGeneric:
    def test_ctz6_is_dec1(self):
        info = ice_to_generic('CTZ6', '2026-06-17')
        assert info is not None
        assert info.generic_code == 'CTDEC1'
        assert info.delivery_year == 2026

    def test_ctz7_is_dec2(self):
        info = ice_to_generic('CTZ7', '2026-06-17')
        assert info is not None
        assert info.generic_code == 'CTDEC2'

    def test_october_excluded(self):
        # CTX6 = Oct options — should return None (excluded)
        # But CTX is also Nov options code; let's test CTV (Oct futures)
        info = ice_to_generic('CTV6', '2026-06-17')
        assert info is None

    def test_outside_position_2_is_none(self):
        # CTZ8 = Dec 2028 = CTDEC3 on 2026-06-17 → out of scope
        info = ice_to_generic('CTZ8', '2026-06-17')
        assert info is None


class TestFuturesPrefixFor:
    def test_serial_ctu(self):
        assert futures_prefix_for('CTU6') == 'CTZ'

    def test_serial_ctx(self):
        assert futures_prefix_for('CTX6') == 'CTZ'

    def test_serial_ctf(self):
        assert futures_prefix_for('CTF7') == 'CTH'

    def test_direct_ctz(self):
        assert futures_prefix_for('CTZ6') == 'CTZ'

    def test_direct_cth(self):
        assert futures_prefix_for('CTH7') == 'CTH'

    def test_direct_ctk(self):
        assert futures_prefix_for('CTK7') == 'CTK'

    def test_direct_ctn(self):
        assert futures_prefix_for('CTN7') == 'CTN'


class TestParseIceCode:
    def test_ctz6(self):
        prefix, letter, year = parse_ice_code('CTZ6', '2026-06-17')
        assert prefix == 'CT'
        assert letter == 'Z'
        assert year == 2026

    def test_cth7(self):
        prefix, letter, year = parse_ice_code('CTH7', '2026-06-17')
        assert letter == 'H'
        assert year == 2027


# ===========================================================================
# 2. Window cutoff helpers
# ===========================================================================

class TestWindowCutoff:
    def test_overnight_end_exclusive(self):
        # inclusive=False → :00 so 07:00:19 snapshot is excluded from overnight
        ts = _window_cutoff('2026-06-17', 7, 0, prev_day=False, inclusive=False)
        assert ts == '2026-06-17 07:00:00'

    def test_day_end_inclusive(self):
        # inclusive=True (default) → :59 so 14:20:07 snapshot is included in day
        ts = _window_cutoff('2026-06-17', 14, 20, prev_day=False, inclusive=True)
        assert ts == '2026-06-17 14:20:59'

    def test_day_start_inclusive(self):
        # inclusive=True (default) → :59 so 07:00:19 snapshot is included as day start
        ts = _window_cutoff('2026-06-17', 7, 0, prev_day=False, inclusive=True)
        assert ts == '2026-06-17 07:00:59'

    def test_prev_day(self):
        ts = _window_cutoff('2026-06-17', 21, 0, prev_day=True)
        assert ts.startswith('2026-06-16')


# ===========================================================================
# 3. _float helper
# ===========================================================================

class TestFloat:
    def test_numeric(self):
        assert _float('100') == 100.0

    def test_empty(self):
        assert _float('') == 0.0

    def test_none(self):
        assert _float(None) == 0.0

    def test_bad(self):
        assert _float('n/a') == 0.0


# ===========================================================================
# 4. _summarise
# ===========================================================================

class TestSummarise:
    def _make_window_data(self, per_generic):
        return {'snapshot_ts': '2026-06-17 06:59:58', 'per_generic': per_generic}

    def test_totals(self):
        pg = {
            'CTDEC1': {'call': 100.0, 'put': 200.0, 'ice_code': 'CTZ6',
                       'delivery_year': 2026, 'month_code': 'Z', 'month_name': 'Dec'},
            'CTDEC2': {'call': 50.0,  'put': 50.0,  'ice_code': 'CTZ7',
                       'delivery_year': 2027, 'month_code': 'Z', 'month_name': 'Dec'},
        }
        s = _summarise(self._make_window_data(pg))
        assert s['total'] == 400.0
        assert s['call']  == 150.0
        assert s['put']   == 250.0
        assert abs(s['pc_ratio'] - 250/150) < 0.001

    def test_empty(self):
        s = _summarise(self._make_window_data({}))
        assert s['total'] == 0.0
        assert s['pc_ratio'] is None

    def test_per_contract_keys(self):
        pg = {
            'CTDEC1': {'call': 10.0, 'put': 5.0, 'ice_code': 'CTZ6',
                       'delivery_year': 2026, 'month_code': 'Z', 'month_name': 'Dec'},
        }
        s = _summarise(self._make_window_data(pg))
        assert 'CTDEC1' in s['per_contract']
        c = s['per_contract']['CTDEC1']
        assert c['ice_code'] == 'CTZ6'
        assert c['delivery_year'] == 2026
        assert c['total'] == 15.0


# ===========================================================================
# 5. RVOL tiers — graceful degradation
# ===========================================================================

class TestComputeRvol:
    def test_full_tiers(self):
        prior = list(range(100, 160))   # 60 values
        rvol = compute_rvol(100.0, prior)
        for tier in config.LOOKBACK_TIERS:
            assert rvol[str(tier)]['note'] is None
            assert rvol[str(tier)]['n'] == tier

    def test_partial_degrades(self):
        prior = [200.0, 300.0, 100.0]   # only 3 sessions
        rvol = compute_rvol(150.0, prior)
        assert rvol['5']['note'] is not None
        assert 'have 3 of 5' in rvol['5']['note']
        assert rvol['10']['note'] is not None
        assert rvol['5']['n'] == 3

    def test_zero_prior(self):
        rvol = compute_rvol(100.0, [])
        for tier in config.LOOKBACK_TIERS:
            assert rvol[str(tier)]['rvol'] is None
            assert rvol[str(tier)]['n'] == 0

    def test_rvol_value(self):
        prior = [100.0] * 60
        rvol = compute_rvol(200.0, prior)
        assert abs(rvol['5']['rvol'] - 2.0) < 0.001
        assert abs(rvol['60']['rvol'] - 2.0) < 0.001


# ===========================================================================
# 6. History — idempotent write + permanent (no deletions)
# ===========================================================================

class TestWriteHistory:
    def _minimal_summary(self, total=500.0):
        return {'total': total, 'call': 300.0, 'put': 200.0, 'pc_ratio': 0.667,
                'per_contract': {}}

    def _minimal_overnight_data(self):
        return {'snapshot_ts': '2026-06-17 06:59:58', 'per_generic': {}}

    def _minimal_day_data(self):
        return {'snapshot_ts_start': '2026-06-17 07:00:19',
                'snapshot_ts_end': '2026-06-17 14:20:07', 'per_generic': {}}

    def test_creates_file_and_writes_row(self, tmp_path):
        with patch.object(config, 'HISTORY_CSV',
                          str(tmp_path / 'history' / 'session_volume_history.csv')):
            os.makedirs(str(tmp_path / 'history'))
            write_history(
                'CT', '2026-06-17',
                self._minimal_summary(), self._minimal_summary(total=800.0),
                self._minimal_overnight_data(), self._minimal_day_data(),
            )
            path = str(tmp_path / 'history' / 'session_volume_history.csv')
            assert os.path.isfile(path)
            with open(path) as fh:
                rows = list(csv.DictReader(fh))
            assert len(rows) == 1
            assert rows[0]['date'] == '2026-06-17'
            assert rows[0]['overnight_total'] == '500.00'
            assert rows[0]['day_total'] == '800.00'

    def test_idempotent_overwrite(self, tmp_path):
        csv_path = str(tmp_path / 'session_volume_history.csv')
        with patch.object(config, 'HISTORY_CSV', csv_path):
            os.makedirs(tmp_path, exist_ok=True)
            # Write once
            write_history('CT', '2026-06-17',
                          self._minimal_summary(100), None,
                          self._minimal_overnight_data(), None)
            # Write again — should overwrite, not duplicate
            write_history('CT', '2026-06-17',
                          self._minimal_summary(999), None,
                          self._minimal_overnight_data(), None)
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
            assert len(rows) == 1
            assert rows[0]['overnight_total'] == '999.00'

    def test_different_dates_both_preserved(self, tmp_path):
        csv_path = str(tmp_path / 'session_volume_history.csv')
        with patch.object(config, 'HISTORY_CSV', csv_path):
            write_history('CT', '2026-06-16',
                          self._minimal_summary(100), None,
                          self._minimal_overnight_data(), None)
            write_history('CT', '2026-06-17',
                          self._minimal_summary(200), None,
                          self._minimal_overnight_data(), None)
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
            assert len(rows) == 2   # permanent — neither deleted

    def test_no_delete_existing_rows(self, tmp_path):
        csv_path = str(tmp_path / 'session_volume_history.csv')
        with patch.object(config, 'HISTORY_CSV', csv_path):
            # Seed 5 rows
            for i in range(5):
                d = f'2026-06-{10+i:02d}'
                write_history('CT', d, self._minimal_summary(i * 100), None,
                              self._minimal_overnight_data(), None)
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
            assert len(rows) == 5


# ===========================================================================
# 7. _prior_sessions
# ===========================================================================

class TestPriorSessions:
    def _history(self):
        return [
            {'date': '2026-06-16', 'commodity': 'CT',
             'overnight_total': '500', 'day_total': '1200'},
            {'date': '2026-06-15', 'commodity': 'CT',
             'overnight_total': '400', 'day_total': '1000'},
            {'date': '2026-06-14', 'commodity': 'CT',
             'overnight_total': '300', 'day_total': '800'},
        ]

    def test_overnight_prior(self):
        prior = _prior_sessions(self._history(), '2026-06-17', 'overnight')
        assert prior == [500.0, 400.0, 300.0]

    def test_day_prior(self):
        prior = _prior_sessions(self._history(), '2026-06-17', 'day')
        assert prior == [1200.0, 1000.0, 800.0]

    def test_excludes_target_date(self):
        prior = _prior_sessions(self._history(), '2026-06-16', 'overnight')
        assert 500.0 not in prior

    def test_empty_when_no_history(self):
        prior = _prior_sessions([], '2026-06-17', 'overnight')
        assert prior == []


# ===========================================================================
# 8. Window extraction from synthetic tape
# ===========================================================================

class TestExtractOvernight:
    def _make_tape(self, tmp_path, date_str='2026-06-17'):
        rows = []
        # Before 07:00 — overnight rows
        for ts in ['2026-06-16 21:00:02', '2026-06-17 06:59:58']:
            rows += [
                _tape_row(ts, date_str, 'CTZ6', 100.0, 150.0, 75.0),
                _tape_row(ts, date_str, 'CTZ6', 50.0,  80.0,  76.0),
                _tape_row(ts, date_str, 'CTH7', 20.0,  30.0,  75.0),
            ]
        # After 07:00 — day rows (should NOT be included in overnight)
        for ts in ['2026-06-17 07:00:19', '2026-06-17 14:20:07']:
            rows += [
                _tape_row(ts, date_str, 'CTZ6', 200.0, 300.0, 75.0),
            ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        return tape_path

    def test_overnight_uses_last_snapshot_before_0700(self, tmp_path):
        tape = self._make_tape(tmp_path)
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape, '2026-06-17', universe)
        assert result is not None
        assert result['snapshot_ts'] == '2026-06-17 06:59:58'

    def test_overnight_sums_strikes_per_generic(self, tmp_path):
        tape = self._make_tape(tmp_path)
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape, '2026-06-17', universe)
        # CTZ6 maps to CTDEC1; two strikes → call=150, put=230
        pg = result['per_generic']
        assert 'CTDEC1' in pg
        assert abs(pg['CTDEC1']['call'] - 150.0) < 0.01
        assert abs(pg['CTDEC1']['put']  - 230.0) < 0.01

    def test_overnight_none_when_no_snapshot_before_0700(self, tmp_path):
        # Tape only has rows after 07:00
        rows = [_tape_row('2026-06-17 07:30:00', '2026-06-17', 'CTZ6', 100, 50)]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        assert result is None

    def test_october_excluded_from_overnight(self, tmp_path):
        rows = [
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTV6', 999, 999),  # Oct — excluded
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTZ6', 100, 50),
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        assert result is not None
        # CTV6 must not appear
        for g in result['per_generic']:
            assert 'OCT' not in g and 'V' not in result['per_generic'][g]['month_code']

    def test_missing_tape_raises_with_path(self, tmp_path):
        import re as _re
        tape_path = str(tmp_path / 'nonexistent.csv')
        universe = build_capture_universe('2026-06-17')
        with pytest.raises(FileNotFoundError, match=_re.escape(str(tmp_path))):
            extract_overnight(tape_path, '2026-06-17', universe)


class TestExtractDay:
    def _make_tape(self, tmp_path, date_str='2026-06-17'):
        rows = []
        # 07:00 snapshot — cumulative at day start
        for ts in ['2026-06-17 07:00:19']:
            rows += [
                _tape_row(ts, date_str, 'CTZ6', 100.0, 200.0),   # cum at 07:00
                _tape_row(ts, date_str, 'CTH7', 10.0,  20.0),
            ]
        # 14:20 snapshot — cumulative at day end
        for ts in ['2026-06-17 14:20:07']:
            rows += [
                _tape_row(ts, date_str, 'CTZ6', 350.0, 500.0),   # cum at 14:20
                _tape_row(ts, date_str, 'CTH7', 40.0,  80.0),
            ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        return tape_path

    def test_day_subtracts_start_from_end(self, tmp_path):
        tape = self._make_tape(tmp_path)
        universe = build_capture_universe('2026-06-17')
        result = extract_day(tape, '2026-06-17', universe)
        assert result is not None
        pg = result['per_generic']
        # CTZ6 → CTDEC1: call=350-100=250, put=500-200=300
        assert abs(pg['CTDEC1']['call'] - 250.0) < 0.01
        assert abs(pg['CTDEC1']['put']  - 300.0) < 0.01

    def test_day_none_when_no_1420_snapshot(self, tmp_path):
        rows = [_tape_row('2026-06-17 07:00:19', '2026-06-17', 'CTZ6', 100, 50)]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_day(tape_path, '2026-06-17', universe)
        assert result is None

    def test_day_vol_never_negative(self, tmp_path):
        # If end < start (shouldn't happen but defensive), clamp to 0
        rows = [
            _tape_row('2026-06-17 07:00:19', '2026-06-17', 'CTZ6', 300.0, 400.0),
            _tape_row('2026-06-17 14:20:07', '2026-06-17', 'CTZ6', 100.0, 200.0),  # < start
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_day(tape_path, '2026-06-17', universe)
        assert result is not None
        for slot in result['per_generic'].values():
            assert slot['call'] >= 0.0
            assert slot['put']  >= 0.0


# ===========================================================================
# 9. Holiday guard
# ===========================================================================

class TestHolidayGuard:
    def test_closed_date_exits_zero(self, capsys):
        rc = process_session('CT', '2026-12-25', 'both', no_write=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert 'closed' in out.lower()

    def test_open_date_not_skipped(self, tmp_path, capsys):
        # A working day should attempt to read the tape (may fail if not present,
        # but should NOT print the "closed" message)
        with patch.object(config, 'OPTIONS_FLOW_DATA', str(tmp_path)):
            rc = process_session('CT', '2026-06-17', 'both', no_write=True)
        out = capsys.readouterr().out
        assert 'closed' not in out.lower()


# ===========================================================================
# 10. Loud-fail with path
# ===========================================================================

class TestLoudFail:
    def test_missing_data_dir_fails_with_path(self, tmp_path, capsys):
        nonexistent = str(tmp_path / 'no_such_dir')
        with patch.object(config, 'OPTIONS_FLOW_DATA', nonexistent):
            rc = process_session('CT', '2026-06-17', 'overnight', no_write=True)
        assert rc == 1
        err = capsys.readouterr().err
        assert nonexistent in err or 'FATAL' in err

    def test_missing_tape_fails_with_path(self, tmp_path, capsys):
        # Dir exists but no tape file inside
        os.makedirs(str(tmp_path / '2026-06-17'))
        with patch.object(config, 'OPTIONS_FLOW_DATA', str(tmp_path)):
            rc = process_session('CT', '2026-06-17', 'overnight', no_write=True)
        assert rc == 1
        err = capsys.readouterr().err
        # The path to the missing tape must appear in the error
        assert '2026-06-17' in err


# ===========================================================================
# 11. --no-write: no files created
# ===========================================================================

class TestNoWrite:
    def _synthetic_tape(self, tmp_path, date_str='2026-06-17'):
        d = tmp_path / date_str
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            _tape_row('2026-06-16 21:00:02', date_str, 'CTZ6', 10, 5),
            _tape_row('2026-06-17 06:59:58', date_str, 'CTZ6', 50, 30),
            _tape_row('2026-06-17 07:00:19', date_str, 'CTZ6', 60, 35),
            _tape_row('2026-06-17 14:20:07', date_str, 'CTZ6', 200, 100),
        ]
        tape_path = str(d / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        return str(tmp_path)

    def test_no_write_creates_no_files(self, tmp_path):
        data_dir = self._synthetic_tape(tmp_path)
        hist_path = str(tmp_path / 'history' / 'session_volume_history.csv')
        out_txt   = str(tmp_path / '2026-06-17' / 'session_volume.txt')

        with (patch.object(config, 'OPTIONS_FLOW_DATA', data_dir),
              patch.object(config, 'HISTORY_CSV', hist_path),
              patch.object(config, 'DATA_DIR', data_dir)):
            process_session('CT', '2026-06-17', 'both', no_write=True)

        assert not os.path.isfile(hist_path)
        assert not os.path.isfile(out_txt)


# ===========================================================================
# 12. Serial option mapping (CTU → CTZ, CTF → CTH)
# ===========================================================================

class TestSerialMapping:
    def test_ctu6_maps_to_ctz_slot(self, tmp_path):
        # CTU6 is a September serial → maps to CTZ (Dec futures)
        rows = [
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTU6', 99.0, 77.0),
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTZ6', 10.0, 5.0),
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        assert result is not None
        pg = result['per_generic']
        # Both CTU6 (serial → CTZ6) and CTZ6 should fold into CTDEC1
        assert 'CTDEC1' in pg
        # Combined: call = 99+10 = 109, put = 77+5 = 82
        assert abs(pg['CTDEC1']['call'] - 109.0) < 0.01
        assert abs(pg['CTDEC1']['put']  - 82.0) < 0.01

    def test_ctf7_maps_to_cth_slot(self, tmp_path):
        # CTF7 is a January serial → maps to CTH (Mar futures)
        rows = [
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTF7', 55.0, 33.0),
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        assert result is not None
        pg = result['per_generic']
        # CTF7 → CTH7 → CTMAR1
        assert 'CTMAR1' in pg
        assert abs(pg['CTMAR1']['call'] - 55.0) < 0.01


# ===========================================================================
# 13. Symbology: delivery_year stored as 4-digit int
# ===========================================================================

class TestDeliveryYear4Digit:
    def test_per_contract_has_4digit_year(self, tmp_path):
        rows = [
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTZ6', 100, 50),
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTZ7', 20,  10),
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        for slot in result['per_generic'].values():
            assert slot['delivery_year'] >= 2026
            assert len(str(slot['delivery_year'])) == 4


# ===========================================================================
# 14. August also excluded (CTQ)
# ===========================================================================

class TestAugustExcluded:
    def test_ctq6_excluded(self, tmp_path):
        rows = [
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTQ6', 999, 999),
            _tape_row('2026-06-17 06:59:58', '2026-06-17', 'CTZ6', 100, 50),
        ]
        tape_path = str(tmp_path / 'ct_options_tape.csv')
        with open(tape_path, 'w', newline='') as fh:
            fh.write(_make_tape_csv(rows))
        universe = build_capture_universe('2026-06-17')
        result = extract_overnight(tape_path, '2026-06-17', universe)
        # CTQ6 (Aug) must not appear anywhere
        for g, slot in result['per_generic'].items():
            assert slot['month_code'] != 'Q', f'Aug (Q) found in {g}'
