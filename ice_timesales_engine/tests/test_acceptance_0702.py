"""
ACCEPTANCE TEST -- the locked, user-verified 2026-07-02 CT Z26 numbers.

Runs the REAL fixture (byte-copy of the on-disk blotter) through the full
pipeline (parse -> normalize -> classify -> load -> aggregate -> query) and
must reproduce exactly:

  night 3,667 / day 14,699 / full 18,366   (config windows, [start,end))
  SetByAsk 6,717  SetByBid 6,008  Leg 4,766  blank 522
  EFS 207  EFS-Delete 99  EFP 47            (outright = 12,725)

By-condition sum == night + day == full (self-validating; the tape needs no
settle file to be correct).
"""

import os
from pathlib import Path

import pytest

from ingest.aggregator import rebuild_minute_agg
from ingest.blotter_parser import read_blotter
from ingest.loader import upsert_ticks
from ingest.normalize import normalize_tick
from store import repository as repo

SESS = '2026-07-02'

EXPECTED = {
    'night': 3667.0, 'day': 14699.0, 'full': 18366.0,
    # outright = SetByAsk 6717 + SetByBid 6008 + blank/unstamped 522 = 13247
    'outright': 13247.0, 'leg': 4766.0,
    'efs': 207.0, 'efs_delete': 99.0, 'efp': 47.0,
}


@pytest.fixture
def loaded_db(tmp_db, fixtures_dir):
    path = Path(fixtures_dir) / 'CT' / SESS / f'futures_blotter_CT_Z26_{SESS}.csv'
    assert path.is_file(), f'fixture missing: {path}'
    rows = [normalize_tick(rt, 'CT', SESS) for rt in read_blotter(path)]
    inserted = upsert_ticks(tmp_db, rows)
    assert inserted == len(rows) == 11601          # verified tick count
    rebuild_minute_agg(tmp_db, 'CT', SESS)
    return tmp_db


class TestAcceptance0702:
    def test_night_day_full(self, loaded_db):
        night = repo.window_sum(loaded_db, 'CT',
                                '2026-07-01T21:00:00', f'{SESS}T07:00:00')
        day = repo.window_sum(loaded_db, 'CT',
                              f'{SESS}T07:00:00', f'{SESS}T14:20:00')
        full = repo.window_sum(loaded_db, 'CT',
                               '2026-07-01T21:00:00', f'{SESS}T14:20:00')
        assert night['all'] == EXPECTED['night']
        assert day['all'] == EXPECTED['day']
        assert full['all'] == EXPECTED['full']
        assert night['all'] + day['all'] == full['all']

    def test_by_condition(self, loaded_db):
        full = repo.window_sum(loaded_db, 'CT',
                               '2026-07-01T21:00:00', f'{SESS}T14:20:00')
        bt = full['by_type']
        for k in ('outright', 'leg', 'efs', 'efs_delete', 'efp'):
            assert bt.get(k, 0.0) == EXPECTED[k], f'{k}: {bt.get(k)} != {EXPECTED[k]}'
        # ask+bid (excl. blank/unstamped) = 12725, the pure-stamped outright vol
        assert EXPECTED['outright'] - 522.0 == 12725.0
        assert sum(bt.values()) == EXPECTED['full']    # self-validating
        assert 'blank' not in bt                        # blank folded into outright

    def test_generic_mapping(self, loaded_db):
        contracts = repo.traded_contracts(loaded_db, 'CT', SESS)
        z26 = next(c for c in contracts if c['ice_code'] == 'CTZ6')
        assert z26['generic_code'] == 'CTDEC1'
        assert z26['total'] == EXPECTED['full']

    def test_clean_total_excludes_efs_delete(self, loaded_db):
        full = repo.window_sum(loaded_db, 'CT',
                               '2026-07-01T21:00:00', f'{SESS}T14:20:00')
        assert full['clean'] == EXPECTED['full'] - EXPECTED['efs_delete']

    def test_type_filter_outright_only(self, loaded_db):
        # outright now includes blank/unstamped fills -> 13,247 (was 12,725
        # when blank was its own bucket).
        full = repo.window_sum(loaded_db, 'CT',
                               '2026-07-01T21:00:00', f'{SESS}T14:20:00',
                               types=['outright'])
        assert full['all'] == 13247.0
