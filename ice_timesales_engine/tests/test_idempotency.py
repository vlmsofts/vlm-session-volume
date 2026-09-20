"""Idempotency + missing-forward tests (edge 3, 13, 19)."""

from pathlib import Path

from ingest import discover
from ingest.blotter_parser import file_sha256, read_blotter
from ingest.loader import IngestMeta, already_ingested, record_ingest, upsert_ticks
from ingest.normalize import normalize_tick

SESS = '2026-07-02'


def _rows(fixtures_dir):
    path = Path(fixtures_dir) / 'CT' / SESS / f'futures_blotter_CT_Z26_{SESS}.csv'
    return [normalize_tick(rt, 'CT', SESS) for rt in read_blotter(path)], path


class TestIdempotency:
    def test_reingest_inserts_zero(self, tmp_db, fixtures_dir):
        rows, _ = _rows(fixtures_dir)
        first = upsert_ticks(tmp_db, rows)
        second = upsert_ticks(tmp_db, rows)
        assert first == len(rows)
        assert second == 0
        n = tmp_db.q('SELECT COUNT(*) FROM ticks')[0][0]
        assert n == len(rows)

    def test_sha256_whole_file_skip(self, tmp_db, fixtures_dir):
        rows, path = _rows(fixtures_dir)
        sha = file_sha256(path)
        assert not already_ingested(tmp_db, 'CT', SESS, 'CTZ6', sha)
        record_ingest(tmp_db, IngestMeta('CT', SESS, 'CTZ6', path.name, sha,
                                         len(rows), len(rows), 'ok'))
        assert already_ingested(tmp_db, 'CT', SESS, 'CTZ6', sha)
        assert not already_ingested(tmp_db, 'CT', SESS, 'CTZ6', 'different-sha')


class TestMissingForward:
    def test_no_day_folder_returns_empty(self, fixture_ice_root):
        assert discover.find_blotter_files('CT', '2026-01-15') == []
        assert discover.find_day_folders('SB') == []

    def test_fixture_day_found(self, fixture_ice_root):
        files = discover.find_blotter_files('CT', SESS)
        assert len(files) == 1
        assert discover.parse_fwd_from_filename(files[0]) == 'Z26'

    def test_holiday_ingest_is_clean_noop(self, tmp_db, fixture_ice_root):
        from jobs.daily_ingest import ingest_day
        summary = ingest_day(tmp_db, 'CT', '2026-07-03')   # verified holiday
        assert summary['status'] == 'holiday'
        assert tmp_db.q('SELECT COUNT(*) FROM ticks')[0][0] == 0

    def test_no_day_folder_is_capture_pending_not_a_zero_day(
            self, tmp_db, fixture_ice_root):
        """CHANGED 2026-09-20. This case used to assert 'no_blotter'. A MISSING
        day folder is not evidence of a zero-volume day -- it is evidence the
        capture has not written anything yet, which is exactly how CC and SB
        lost 2026-09-18. 'no_blotter' now requires a landed capture; see the
        next test."""
        from jobs.daily_ingest import ingest_day
        summary = ingest_day(tmp_db, 'CT', '2026-06-15')   # weekday, no folder
        assert summary['status'] == 'capture_pending'
        assert tmp_db.q('SELECT COUNT(*) FROM ticks')[0][0] == 0

    def test_landed_capture_with_no_blotters_is_a_zero_volume_day(
            self, tmp_db, monkeypatch, tmp_path):
        """The by-design branch, unchanged: the capture ran, has gone QUIET,
        and no futures traded -> a final zero.

        The artifact is back-dated past discover.CAPTURE_QUIESCE_SECONDS on
        purpose. A freshly-written folder is a capture still in flight, which
        is the case the next test pins."""
        import os
        import time

        import config
        from ingest import discover
        from jobs.daily_ingest import ingest_day
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        day = tmp_path / 'CT' / '2026-06-15'
        day.mkdir(parents=True)
        f = day / 'futures_settle_2026-06-15.csv'
        f.write_text('x')
        old = time.time() - discover.CAPTURE_QUIESCE_SECONDS - 60
        os.utime(f, (old, old))
        summary = ingest_day(tmp_db, 'CT', '2026-06-15')
        assert summary['status'] == 'no_blotter'
        assert tmp_db.q('SELECT COUNT(*) FROM ticks')[0][0] == 0

    def test_folder_still_being_written_to_is_capture_pending(
            self, tmp_db, monkeypatch, tmp_path):
        """A day folder whose newest file was written seconds ago is an ACTIVE
        capture, not a zero-volume day -- the 2026-09-18 17:10 shape."""
        import config
        from jobs.daily_ingest import ingest_day
        monkeypatch.setattr(config, 'ICE_ROOT', str(tmp_path))
        day = tmp_path / 'CT' / '2026-06-15'
        day.mkdir(parents=True)
        (day / 'futures_settle_2026-06-15.csv').write_text('x')   # mtime = now
        summary = ingest_day(tmp_db, 'CT', '2026-06-15')
        assert summary['status'] == 'capture_pending'
        assert tmp_db.q('SELECT COUNT(*) FROM ticks')[0][0] == 0
