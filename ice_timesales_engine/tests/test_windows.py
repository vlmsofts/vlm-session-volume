"""Window-resolution tests -- presets, custom, cross-midnight (edge 7-9)."""

import pytest

from api.windows import resolve, resolve_custom, resolve_preset


class TestPresets:
    def test_night(self):
        s, e = resolve_preset('night', '2026-07-02')
        assert s == '2026-07-01T21:00:00'
        assert e == '2026-07-02T07:00:00'

    def test_day(self):
        s, e = resolve_preset('day', '2026-07-02')
        assert s == '2026-07-02T07:00:00'
        assert e == '2026-07-02T14:20:00'

    def test_full(self):
        s, e = resolve_preset('full', '2026-07-02')
        assert s == '2026-07-01T21:00:00'
        assert e == '2026-07-02T14:20:00'

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            resolve_preset('lunch', '2026-07-02')


class TestCustom:
    def test_intraday(self):
        s, e = resolve_custom('09:15', '10:30', '2026-07-02')
        assert s == '2026-07-02T09:15:00'
        assert e == '2026-07-02T10:30:00'

    def test_overnight_start_anchors_prev_day(self):
        s, e = resolve_custom('21:00', '02:00', '2026-07-02')
        assert s == '2026-07-01T21:00:00'
        assert e == '2026-07-02T02:00:00'

    def test_cross_midnight_by_clock_order(self):
        s, e = resolve_custom('23:00', '01:00', '2026-07-02')
        assert s == '2026-07-01T23:00:00'
        assert e == '2026-07-02T01:00:00'

    def test_empty_window_raises(self):
        with pytest.raises(ValueError):
            resolve_custom('10:00', '10:00', '2026-07-02')


class TestResolve:
    def test_requires_preset_or_bounds(self):
        with pytest.raises(ValueError):
            resolve('2026-07-02')

    def test_preset_path(self):
        w = resolve('2026-07-02', preset='day')
        assert w['start'] == '2026-07-02T07:00:00'
