"""Normalization + window assignment unit tests (edge-case register 1, 7-9)."""

from datetime import date, datetime

import pytest

from ingest.normalize import assign_window, normalize_contract, to_generic

SESS = date(2026, 7, 2)


class TestNormalizeContract:
    def test_space_two_digit_year(self):
        assert normalize_contract('CT Z26') == 'CTZ6'
        assert normalize_contract('CT H27') == 'CTH7'
        assert normalize_contract('CT K27') == 'CTK7'

    def test_already_normalized_passthrough(self):
        assert normalize_contract('CTZ26') == 'CTZ6'

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            normalize_contract('CT')
        with pytest.raises(ValueError):
            normalize_contract('CT ZXX')


class TestToGeneric:
    def test_in_universe(self):
        assert to_generic('CTZ6', '2026-07-02', 'CT') == 'CTDEC1'
        assert to_generic('CTZ7', '2026-07-02', 'CT') == 'CTDEC2'
        assert to_generic('CTH7', '2026-07-02', 'CT') == 'CTMAR1'

    def test_october_august_excluded(self):
        assert to_generic('CTV6', '2026-07-02', 'CT') is None
        assert to_generic('CTQ6', '2026-07-02', 'CT') is None

    def test_beyond_position_two(self):
        assert to_generic('CTZ8', '2026-07-02', 'CT') is None


class TestAssignWindow:
    def test_prior_evening_is_night(self):
        assert assign_window(datetime(2026, 7, 1, 21, 0, 0), SESS) == 'night'
        assert assign_window(datetime(2026, 7, 1, 23, 59, 59), SESS) == 'night'

    def test_session_date_pre_0700_is_night(self):
        assert assign_window(datetime(2026, 7, 2, 0, 0, 0), SESS) == 'night'
        assert assign_window(datetime(2026, 7, 2, 6, 59, 59), SESS) == 'night'

    def test_0700_boundary_is_day(self):
        assert assign_window(datetime(2026, 7, 2, 7, 0, 0), SESS) == 'day'

    def test_day_window(self):
        assert assign_window(datetime(2026, 7, 2, 14, 19, 59), SESS) == 'day'

    def test_1420_and_after_is_other(self):
        assert assign_window(datetime(2026, 7, 2, 14, 20, 0), SESS) == 'other'
        assert assign_window(datetime(2026, 7, 2, 16, 0, 0), SESS) == 'other'

    def test_before_prior_2100_is_other(self):
        assert assign_window(datetime(2026, 7, 1, 20, 59, 59), SESS) == 'other'
