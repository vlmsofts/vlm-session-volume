"""Hermetic tests for the catch-up selection rule and the capture_landed
discriminator that stopped CC/SB silently losing 2026-09-18.

Nothing here touches the database, the ICE root or the network: the selection
rule is a pure function over a fake filesystem listing and a fake set of DB
keys, exactly so this can be run anywhere.

The fixture is deliberately the UNFAVOURABLE one. It does not only contain the
case the code is supposed to pick up (2026-09-18 CC/SB); it also contains, for
every commodity, a GENUINE zero-volume holiday-adjacent day -- 2026-07-03,
which really does have day folders on disk holding settle/settled_surface/
spreads files but zero futures blotters, and which therefore really does have
no rows in the database and never will. If the selection rule keyed on "DB has
no rows" alone, that day would be selected on every single run forever. The
test below proves it is not.
"""

import os

import pytest

from ingest import discover
from jobs.catchup_ingest import select_catchup_days, trading_days_back

CMDS = ('CT', 'KC', 'SB', 'CC')

# The real shape of the 2026-09-18 incident, plus its neighbours.
#   09-17  everything captured and ingested        -> nothing to do
#   09-18  all four captured; CT/KC ingested, CC/SB NOT   -> the two to catch
#   07-03  ICE holiday: folders exist, zero futures blotters, zero DB rows
#          -> must NEVER be selected, however often this job runs
HOLIDAY = '2026-07-03'
CANDIDATE_DAYS = ['2026-07-03', '2026-09-17', '2026-09-18']

BLOTTER_COUNTS = {
    # holiday-adjacent genuine zero-volume day: captured, but no futures traded
    ('CT', HOLIDAY): 0, ('KC', HOLIDAY): 0, ('SB', HOLIDAY): 0, ('CC', HOLIDAY): 0,
    # a normal, fully-ingested day
    ('CT', '2026-09-17'): 7, ('KC', '2026-09-17'): 7,
    ('SB', '2026-09-17'): 11, ('CC', '2026-09-17'): 9,
    # the incident: all four captured
    ('CT', '2026-09-18'): 7, ('KC', '2026-09-18'): 7,
    ('SB', '2026-09-18'): 11, ('CC', '2026-09-18'): 9,
}

# What the DB held at 17:10 on 09-18: CT and KC only. The holiday has no rows
# for anybody -- correctly, and permanently.
DB_KEYS = {
    ('CT', '2026-09-17'), ('KC', '2026-09-17'),
    ('SB', '2026-09-17'), ('CC', '2026-09-17'),
    ('CT', '2026-09-18'), ('KC', '2026-09-18'),
}

CANDIDATES = [(c, d) for c in CMDS for d in CANDIDATE_DAYS]


class TestSelection:
    def test_picks_exactly_the_missing_captured_days(self):
        todo = select_catchup_days(CANDIDATES, BLOTTER_COUNTS, DB_KEYS, {HOLIDAY})
        assert todo == [('CC', '2026-09-18'), ('SB', '2026-09-18')]

    def test_zero_volume_holiday_day_is_never_selected(self):
        """The anti-spin property: no blotter files -> not selected, ever."""
        todo = select_catchup_days(CANDIDATES, BLOTTER_COUNTS, DB_KEYS, {HOLIDAY})
        assert all(day != HOLIDAY for _, day in todo)

    def test_zero_volume_day_not_selected_even_when_not_flagged_closed(self):
        """Belt and braces: the file-count guard alone must stop it, with the
        closed-dates list empty -- so a calendar that has not been updated for
        a new holiday still cannot make this job spin."""
        todo = select_catchup_days(CANDIDATES, BLOTTER_COUNTS, DB_KEYS, closed_dates=())
        assert all(day != HOLIDAY for _, day in todo)
        assert todo == [('CC', '2026-09-18'), ('SB', '2026-09-18')]

    def test_is_idempotent_once_the_catchup_has_run(self):
        """After ingesting, a second pass selects nothing -- it does not loop."""
        first = select_catchup_days(CANDIDATES, BLOTTER_COUNTS, DB_KEYS, {HOLIDAY})
        after = DB_KEYS | set(first)
        assert select_catchup_days(CANDIDATES, BLOTTER_COUNTS, after, {HOLIDAY}) == []

    def test_missing_count_key_is_treated_as_zero_files(self):
        """A commodity-day with no entry in the listing is not captured."""
        todo = select_catchup_days([('CC', '2026-09-18')], {}, set(), ())
        assert todo == []

    def test_sabotage_the_holiday_is_otherwise_a_live_candidate(self):
        """Guard-the-guard. Feed the SAME function the same fixture with only
        the blotter counts falsified (as if files had been captured on the
        holiday) and the holiday IS selected for all four commodities. So the
        assertions above are exercising the file-count guard, not passing by
        accident because the holiday was excluded some other way."""
        sabotaged = dict(BLOTTER_COUNTS)
        for c in CMDS:
            sabotaged[(c, HOLIDAY)] = 7
        todo = select_catchup_days(CANDIDATES, sabotaged, DB_KEYS, closed_dates=())
        assert [(c, d) for c, d in todo if d == HOLIDAY] == \
            [('CC', HOLIDAY), ('CT', HOLIDAY), ('KC', HOLIDAY), ('SB', HOLIDAY)]


class TestCaptureLanded:
    """The discriminator daily_ingest uses to refuse to record a final zero.

    Three conditions, all required: folder exists, holds >=1 file, and the
    NEWEST file has gone quiet (mtime at least CAPTURE_QUIESCE_SECONDS old).
    `now` is injected throughout so nothing here sleeps or depends on the
    wall clock.
    """

    MIN = 60
    QUIET = discover.CAPTURE_QUIESCE_SECONDS

    def _day(self, tmp_path, cmd, day, files, age_seconds, now):
        """Build a day folder whose newest file is `age_seconds` old."""
        d = tmp_path / cmd / day
        os.makedirs(d, exist_ok=True)
        for name in files:
            p = d / name
            p.write_text('x')
            os.utime(p, (now - age_seconds, now - age_seconds))
        return d

    def test_missing_day_folder_means_capture_has_not_landed(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        assert discover.capture_landed('CC', '2026-09-18', now=1_000_000.0) is False

    def test_empty_day_folder_means_capture_has_not_landed(self, monkeypatch, tmp_path):
        import config
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        os.makedirs(tmp_path / 'CC' / '2026-09-18')
        assert discover.capture_landed('CC', '2026-09-18', now=1_000_000.0) is False

    def test_newest_file_two_minutes_old_is_still_capture_pending(
            self, monkeypatch, tmp_path):
        """THE AUDIT CASE. A folder holding a settle file while the blotters
        are still streaming must NOT read as landed -- otherwise daily_ingest
        records a permanent zero-volume day for a session that is mid-capture.
        This is exactly the 2026-09-18 shape at 17:10."""
        import config
        now = 1_000_000.0
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        self._day(tmp_path, 'CC', '2026-09-18',
                  [f'futures_settle_2026-09-18.csv'], 2 * self.MIN, now)
        assert discover.capture_landed('CC', '2026-09-18', now=now) is False

    def test_newest_file_twenty_minutes_old_with_zero_blotters_is_a_zero_day(
            self, monkeypatch, tmp_path):
        """THE AUDIT CASE, other side. Capture has gone quiet and wrote no
        futures blotters -> a genuine no-trade day, recorded as final."""
        import config
        now = 1_000_000.0
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        self._day(tmp_path, 'CT', HOLIDAY,
                  [f'futures_settle_{HOLIDAY}.csv', f'spreads_{HOLIDAY}.csv'],
                  20 * self.MIN, now)
        assert discover.capture_landed('CT', HOLIDAY, now=now) is True
        assert discover.find_blotter_files('CT', HOLIDAY) == []

    def test_the_newest_file_governs_not_the_oldest(self, monkeypatch, tmp_path):
        """An old settle file next to a blotter written seconds ago is an
        ACTIVE capture. Keying on the oldest (or on mere existence) would
        call this landed."""
        import config
        now = 1_000_000.0
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        d = self._day(tmp_path, 'SB', '2026-09-18',
                      ['futures_settle_2026-09-18.csv'], 60 * self.MIN, now)
        fresh = d / 'futures_blotter_SB_H27_2026-09-18.csv'
        fresh.write_text('x')
        os.utime(fresh, (now - 30, now - 30))
        assert discover.capture_landed('SB', '2026-09-18', now=now) is False

    def test_boundary_exactly_at_the_quiesce_threshold_is_landed(
            self, monkeypatch, tmp_path):
        import config
        now = 1_000_000.0
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        self._day(tmp_path, 'CT', '2026-09-18', ['futures_settle.csv'],
                  self.QUIET, now)
        assert discover.capture_landed('CT', '2026-09-18', now=now) is True
        # one second inside the window is not
        self._day(tmp_path, 'KC', '2026-09-18', ['futures_settle.csv'],
                  self.QUIET - 1, now)
        assert discover.capture_landed('KC', '2026-09-18', now=now) is False


class TestTradingDaysBack:
    def test_skips_weekends_and_holidays_and_includes_today(self):
        from datetime import date
        # Fri 2026-09-18 back 5: 09-14..09-18 (Mon-Fri), no holiday in range.
        assert trading_days_back(5, date(2026, 9, 18)) == [
            '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']

    def test_holiday_is_excluded_from_the_window(self):
        from datetime import date
        # 2026-09-07 is Labor Day (in CT_CLOSED_DATES).
        days = trading_days_back(5, date(2026, 9, 9))
        assert '2026-09-07' not in days
        assert days == ['2026-09-01', '2026-09-02', '2026-09-03',
                        '2026-09-04', '2026-09-08', '2026-09-09'][-5:]


@pytest.mark.parametrize('n', [1, 3, 10])
def test_trading_days_back_returns_exactly_n(n):
    assert len(trading_days_back(n)) == n
