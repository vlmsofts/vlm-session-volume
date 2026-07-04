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

    def test_no_blotter_day_is_zero_not_failure(self, tmp_db, fixture_ice_root):
        from jobs.daily_ingest import ingest_day
        summary = ingest_day(tmp_db, 'CT', '2026-06-15')   # weekday, no folder
        assert summary['status'] == 'no_blotter'
