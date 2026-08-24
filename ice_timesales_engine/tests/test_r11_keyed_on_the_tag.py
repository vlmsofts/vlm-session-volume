"""
R11 WIDENED TO THE TAG -- sabotage suite (2026-08-24 ruling).

The first R11 pass keyed on the bucket name 'efs_delete'. Because the ladder
puts BlockTrde above Leg above the aggressor tags, that silently missed every
cancelled print that was not an EFS. Measured over the whole CT blotter corpus:
922 cancelled lots excluded, 340 STILL COUNTING ('BlockTrde, Leg, Delete' 186,
'BlockTrde, Delete' 127, 'EFP, Delete' 22, 'Leg, Delete' 5). Delete is
orthogonal to trade type: a cancelled block is cancelled.

Two directions, both required -- a guard that only proves it FIRES is half a
guard. The expensive failure here is OVER-firing: ICE counts plain EFS, plain
EFP, plain BlockTrde and plain Leg in official volume (verified exact against
the Daily Market Report: 17/17, 315/315, 100/100). Deleting real flow would be
a worse bug than the one being fixed.

Synthetic ticks only -- no production path, no real blotter, no ICE call.
"""

import pytest

from ingest.classifier import (CANCELLED_TYPES, EXCLUDED_FROM_CLEAN,
                               base_type_of, cancelled_type_for, clean_split,
                               is_cancelled, is_excluded, primary_type,
                               tokenize)
from ingest.rollup import _window_sums
from store import repository as repo

CMD = 'CT'
SESS = '2026-07-02'
START = '2026-07-01T21:00:00'
END = f'{SESS}T14:20:00'

# The four real cancelled shapes from the corpus, at their real lot counts,
# alongside their live twins. Distinct sizes so a wrong bucket is unmistakable.
TICKS = [
    (f'{SESS}T09:00:00', 500.0, 'SetByBid'),                # live outright
    (f'{SESS}T09:01:00', 60.0, 'Leg'),                      # live leg
    (f'{SESS}T09:01:30', 17.0, 'EFS'),                      # live EFS -- counts
    (f'{SESS}T09:01:45', 8.0, 'EFP'),                       # live EFP -- counts
    (f'{SESS}T09:01:50', 300.0, 'BlockTrde'),               # live block -- counts
    (f'{SESS}T09:02:31', 922.0, 'EFS, Delete'),             # cancelled EFS
    (f'{SESS}T09:03:00', 186.0, 'BlockTrde, Leg, Delete'),  # cancelled block
    (f'{SESS}T09:03:10', 127.0, 'BlockTrde, Delete'),       # cancelled block
    (f'{SESS}T09:03:20', 22.0, 'EFP, Delete'),              # cancelled EFP
    (f'{SESS}T09:03:30', 5.0, 'Leg, Delete'),               # cancelled leg
]

LIVE = 500.0 + 60.0 + 17.0 + 8.0 + 300.0            # 885
CANCELLED = 922.0 + 186.0 + 127.0 + 22.0 + 5.0      # 1262 -- full corpus figure
ALL_IN = LIVE + CANCELLED                           # 2147
# What the OLD narrow rule let through: everything but the EFS bust.
LEAKED_BY_OLD_RULE = 186.0 + 127.0 + 22.0 + 5.0     # 340


def _load(db, ticks=TICKS, ice='CTZ6'):
    rows = [(CMD, SESS, ice, 'CTDEC1', ts, 77.0, size,
             primary_type(tokenize(cond)), cond, 1000 + i, 'day',
             '2026-07-02T00:00:00')
            for i, (ts, size, cond) in enumerate(ticks)]
    db.execmany(
        'INSERT INTO ticks (commodity, session_date, ice_code, generic_code,'
        ' exchange_time, price, size, primary_type, conditions_raw, seq_num,'
        ' window_preset, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        rows)
    db.commit()
    from ingest.aggregator import rebuild_minute_agg
    rebuild_minute_agg(db, CMD, SESS)
    return db


@pytest.fixture
def db(tmp_db):
    return _load(tmp_db)


class TestTheLadderCarriesTheTag:
    """Why a bucket per base type: minute_agg/bar5m key on primary_type ALONE,
    so a tag that is not in the bucket cannot be expressed there at all."""

    def test_every_cancelled_shape_gets_its_own_bucket(self):
        assert primary_type(tokenize('EFS, Delete')) == 'efs_delete'
        assert primary_type(tokenize('BlockTrde, Delete')) == 'block_delete'
        assert primary_type(tokenize('BlockTrde, Leg, Delete')) == 'block_delete'
        assert primary_type(tokenize('EFP, Delete')) == 'efp_delete'
        assert primary_type(tokenize('Leg, Delete')) == 'leg_delete'
        assert primary_type(tokenize('SetByBid, Delete')) == 'outright_delete'

    def test_the_historic_name_is_preserved(self):
        """Stored rows and existing types=['efs_delete'] queries stay valid."""
        assert 'efs_delete' in EXCLUDED_FROM_CLEAN
        assert cancelled_type_for('efs') == 'efs_delete'
        assert base_type_of('efs_delete') == 'efs'

    def test_base_and_cancelled_round_trip(self):
        for base in ('efs', 'efp', 'block', 'leg', 'outright', 'other'):
            assert base_type_of(cancelled_type_for(base)) == base


class TestGuardFires:
    """Direction 1: no cancelled print reaches a default total, whatever type."""

    def test_window_sum_excludes_all_1262_cancelled_lots(self, db):
        w = repo.window_sum(db, CMD, START, END)
        assert w['all'] == ALL_IN
        assert w['clean'] == LIVE
        assert w['excluded'] == CANCELLED

    def test_the_340_lots_the_old_rule_leaked_are_now_excluded(self, db):
        """The precise regression: cancelled non-EFS lots must not count."""
        w = repo.window_sum(db, CMD, START, END)
        leaked = sum(v for t, v in w['excluded_by_type'].items()
                     if t != 'efs_delete')
        assert leaked == LEAKED_BY_OLD_RULE
        assert w['clean'] == LIVE, 'a cancelled block must not sit in clean'

    def test_rollup_window_sums_excludes_every_cancelled_bucket(self, db):
        per = _window_sums(db, CMD, SESS)['CTZ6']
        assert per['day'] == LIVE
        assert per['excluded'] == CANCELLED

    def test_traded_contracts_total_is_clean(self, db):
        z = next(c for c in repo.traded_contracts(db, CMD, SESS)
                 if c['ice_code'] == 'CTZ6')
        assert z['total'] == LIVE

    def test_profile_default_excludes_every_cancelled_bucket(self, db):
        bars = repo.profile(db, CMD, START, END, 'full')
        assert sum(b['sum_size'] for b in bars) == LIVE


class TestGuardDoesNotOverFire:
    """Direction 2: ICE counts plain EFS/EFP/Block/Leg, so we must too."""

    @pytest.mark.parametrize('bucket,lots', [
        ('outright', 500.0), ('leg', 60.0), ('efs', 17.0),
        ('efp', 8.0), ('block', 300.0),
    ])
    def test_live_flow_survives_every_default_path(self, db, bucket, lots):
        w = repo.window_sum(db, CMD, START, END)
        assert w['by_type'][bucket] == lots, f'{bucket} must still count'
        assert not is_excluded(bucket)

    def test_untagging_delete_puts_every_lot_straight_back(self, tmp_db):
        """Excluded for being CANCELLED, not for being a block/EFP/leg."""
        untagged = [(ts, size, cond.replace(', Delete', ''))
                    for ts, size, cond in TICKS]
        db = _load(tmp_db, ticks=untagged)
        w = repo.window_sum(db, CMD, START, END)
        assert w['excluded'] == 0.0
        assert w['clean'] == ALL_IN

    def test_a_plain_block_is_never_touched(self, tmp_db):
        """The 300-lot live block is the one most at risk of over-firing."""
        db = _load(tmp_db, ticks=[(f'{SESS}T09:00:00', 300.0, 'BlockTrde')])
        w = repo.window_sum(db, CMD, START, END)
        assert w['clean'] == 300.0
        assert w['excluded'] == 0.0


class TestRetrievability:
    """Requirement 1: cancelled volume stays queryable across every bucket."""

    def test_all_1262_lots_are_retrievable_by_asking_for_cancelled(self, db):
        w = repo.window_sum(db, CMD, START, END, types=list(CANCELLED_TYPES))
        assert w['all'] == CANCELLED, 'the whole cancelled set, not just the EFS'

    def test_a_single_cancelled_bucket_is_still_retrievable(self, db):
        w = repo.window_sum(db, CMD, START, END, types=['block_delete'])
        assert w['all'] == 186.0 + 127.0

    def test_the_legacy_query_still_works(self, db):
        """types=['efs_delete'] must not have been broken by the widening."""
        w = repo.window_sum(db, CMD, START, END, types=['efs_delete'])
        assert w['all'] == 922.0

    def test_cancelled_flow_is_retrievable_from_the_profile_too(self, db):
        bars = repo.profile(db, CMD, START, END, 'full',
                            types=list(CANCELLED_TYPES))
        assert sum(b['sum_size'] for b in bars) == CANCELLED


class TestExcludedAccounting:
    """Requirement 2: P6.6 -- the 340 lots must not silently vanish."""

    def test_every_cancelled_lot_stays_attributable_by_type(self, db):
        w = repo.window_sum(db, CMD, START, END)
        assert w['excluded_by_type'] == {
            'efs_delete': 922.0, 'block_delete': 313.0,
            'efp_delete': 22.0, 'leg_delete': 5.0}

    def test_conservation_holds_across_every_bucket(self, db):
        w = repo.window_sum(db, CMD, START, END)
        assert w['clean'] + w['excluded'] == w['all']
        assert sum(w['by_type'].values()) == w['all']

    def test_rollup_reports_the_excluded_half(self, db):
        assert _window_sums(db, CMD, SESS)['CTZ6']['excluded'] == CANCELLED

    def test_clean_split_conserves_a_mixed_cancelled_mapping(self):
        by_type = {'outright': 13247.0, 'leg': 4766.0, 'efs': 207.0,
                   'efs_delete': 922.0, 'block_delete': 313.0,
                   'efp_delete': 22.0, 'leg_delete': 5.0}
        clean, excluded, by = clean_split(by_type)
        assert clean + excluded == sum(by_type.values())
        assert excluded == CANCELLED
        assert set(by) == {'efs_delete', 'block_delete', 'efp_delete',
                           'leg_delete'}


class TestOneRulingOneMechanism:
    """is_cancelled is an ALIAS, never a second rule."""

    def test_is_cancelled_and_is_excluded_are_the_same_rule(self):
        assert is_cancelled is is_excluded
        assert tuple(CANCELLED_TYPES) == tuple(EXCLUDED_FROM_CLEAN)
