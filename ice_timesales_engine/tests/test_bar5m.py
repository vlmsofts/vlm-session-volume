"""bar5m archive + Bloomberg condition-map tests.

The mapping expectations are the LIVE-verified ones from the 2026-07-01/02
Bloomberg-vs-ICE reconciliation (exact to the lot on 4 contract-days).
"""

import pytest

from ingest.bar5m import bucket_window, floor_5m, replace_bloomberg_day, rollup_ice_bar5m
from ingest.bbg_map import map_bbg_conditions


# ---------- condition map (verified vocabulary) ----------

@pytest.mark.parametrize('cc,expected', [
    ('', 'outright'),                 # blank = real outright fill
    ('NDOO', 'outright'),
    ('NDOT', 'outright'),
    ('ST,NDOO', 'leg'),               # spread-traded
    ('ST,RFC,NDOO', 'leg'),
    ('EFS', 'efs'),
    ('P', 'efp'),
    ('*X', 'efs_delete'),
    ('B,NDOO', 'block'),              # 07-01: 63 lots, matched ICE exactly
    ('I', 'other'),                   # residual code -- never absorbed
    ('ZZ,Q', 'other'),                # unknown vocabulary -> other
])
def test_map_bbg_conditions(cc, expected):
    assert map_bbg_conditions(cc) == expected


def test_map_precedence_efs_delete_beats_everything():
    assert map_bbg_conditions('*X,ST,NDOO') == 'efs_delete'


def test_map_p_only_alone_is_efp():
    # 'P' combined with other codes is NOT efp (only the lone 'P' was verified)
    assert map_bbg_conditions('P') == 'efp'
    assert map_bbg_conditions('P,ST') == 'leg'


# ---------- 5-minute flooring + window (boundaries are 5-min aligned) ----------

@pytest.mark.parametrize('ts,bucket', [
    ('2026-07-02T09:34:59', '2026-07-02T09:30'),
    ('2026-07-02T09:35:00', '2026-07-02T09:35'),
    ('2026-07-01T21:00:00', '2026-07-01T21:00'),
    ('2026-07-02T06:59:59', '2026-07-02T06:55'),
])
def test_floor_5m(ts, bucket):
    assert floor_5m(ts) == bucket


@pytest.mark.parametrize('bucket,window', [
    ('2026-07-01T21:00', 'night'),    # night open
    ('2026-07-02T06:55', 'night'),    # last night bucket
    ('2026-07-02T07:00', 'day'),      # day open (07:00 tick is DAY)
    ('2026-07-02T14:15', 'day'),      # last day bucket
    ('2026-07-02T14:20', 'other'),    # at/after close
])
def test_bucket_window(bucket, window):
    assert bucket_window(bucket, '2026-07-02') == window


# ---------- rollup + seed-writer semantics ----------

def _insert_minute(db, minute_ts, ptype, size, n, ice='CTZ6'):
    db.exec("""
        INSERT INTO minute_agg (commodity, session_date, ice_code, generic_code,
                                minute_ts, primary_type, sum_size, trade_count)
        VALUES ('CT','2026-07-02',%s,'CTDEC1',%s,%s,%s,%s)
    """, (ice, minute_ts, ptype, size, n))
    db.commit()


def test_rollup_ice_bar5m_sums_and_windows(tmp_db):
    # three minutes inside one 5-min bucket + one in the next, mixed types
    _insert_minute(tmp_db, '2026-07-02T09:30', 'outright', 10, 3)
    _insert_minute(tmp_db, '2026-07-02T09:31', 'outright', 5, 2)
    _insert_minute(tmp_db, '2026-07-02T09:31', 'leg', 7, 1)
    _insert_minute(tmp_db, '2026-07-02T09:35', 'outright', 4, 1)
    n = rollup_ice_bar5m(tmp_db, 'CT', '2026-07-02')
    assert n == 3                                     # (0930,out) (0930,leg) (0935,out)
    rows = {(r[0], r[1]): (r[2], r[3], r[4]) for r in tmp_db.q("""
        SELECT bucket_ts, primary_type, sum_size, trade_count, window_preset
        FROM bar5m WHERE source='ice'""")}
    assert rows[('2026-07-02T09:30', 'outright')] == (15, 5, 'day')
    assert rows[('2026-07-02T09:30', 'leg')] == (7, 1, 'day')
    assert rows[('2026-07-02T09:35', 'outright')] == (4, 1, 'day')


def test_rollup_is_idempotent(tmp_db):
    _insert_minute(tmp_db, '2026-07-02T09:30', 'outright', 10, 3)
    rollup_ice_bar5m(tmp_db, 'CT', '2026-07-02')
    rollup_ice_bar5m(tmp_db, 'CT', '2026-07-02')      # re-run = same rows
    (cnt,), = tmp_db.q("SELECT COUNT(*) FROM bar5m WHERE source='ice'")
    assert cnt == 1


def test_bloomberg_writer_is_isolated_and_idempotent(tmp_db):
    # ICE row present; bloomberg writes must not disturb it
    _insert_minute(tmp_db, '2026-07-02T09:30', 'outright', 10, 3)
    rollup_ice_bar5m(tmp_db, 'CT', '2026-07-02')
    buckets = {('2026-07-02T09:30', 'outright'): (99.0, 7)}
    replace_bloomberg_day(tmp_db, 'CT', '2026-07-02', 'CTZ6', 'CTDEC1', buckets)
    replace_bloomberg_day(tmp_db, 'CT', '2026-07-02', 'CTZ6', 'CTDEC1', buckets)
    rows = tmp_db.q("""
        SELECT source, sum_size FROM bar5m
        WHERE session_date='2026-07-02' ORDER BY source""")
    assert rows == [('bloomberg', 99.0), ('ice', 10.0)]
