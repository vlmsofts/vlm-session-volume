"""
R11 SABOTAGE SUITE -- cancelled prints never count, plain EFS/EFP always do.

Two directions, both required. A guard that only proves it FIRES is half a
guard: the expensive failure mode here is over-firing and silently deleting
real flow, because ICE counts plain EFS and plain EFP in official volume
(verified exact against the Daily Market Report: 17/17, 315/315, 100/100).

  FIRES      -- a Delete-tagged print must never reach a default total.
                Every fixture below would inflate a total by exactly 99 lots
                if the exclusion were removed, so each assertion goes RED
                against the pre-fix code and GREEN after.
  NEVER OVER-FIRES -- plain 'EFS' and plain 'EFP' prints must survive every
                default path untouched. Removing the Delete-tag from a fixture
                row must put its lots straight back into the total.

Also covers _window_sums, which had NO test at all. That absence, not the
missing filter, is the root cause: the rollup layer drifted from the
repository layer for as long as nothing asserted they agreed.

Synthetic ticks only -- no production path, no real blotter, no ICE call.
"""

import pytest

from ingest.classifier import (EXCLUDED_FROM_CLEAN, clean_split, is_excluded,
                               primary_type, tokenize)
from ingest.rollup import _window_sums
from store import repository as repo

CMD = 'CT'
SESS = '2026-07-02'
NIGHT_START = '2026-07-01T21:00:00'
DAY_END = f'{SESS}T14:20:00'

# One synthetic session. Sizes are deliberately distinct primes-ish so any
# wrong bucket shows up as a unique number rather than a plausible total.
#   clean day flow   : 500 outright + 60 leg + 17 efs + 8 efp = 585
#   clean night flow : 200 outright                            = 200
#   cancelled        : 4 + 95 = 99   <- the 2026-07-02 CTZ6 event, real shape
TICKS = [
    # (exchange_time, size, conditions_raw, window_preset)
    (f'{SESS}T02:00:00', 200.0, 'SetByAsk', 'night'),
    (f'{SESS}T09:00:00', 500.0, 'SetByBid', 'day'),
    (f'{SESS}T09:01:00', 60.0, 'Leg', 'day'),
    (f'{SESS}T09:01:30', 17.0, 'EFS', 'day'),          # plain EFS -- MUST count
    (f'{SESS}T09:01:45', 8.0, 'EFP', 'day'),           # plain EFP -- MUST count
    (f'{SESS}T09:02:31', 4.0, 'EFS, Delete', 'day'),   # busted -- MUST NOT count
    (f'{SESS}T09:02:31', 95.0, 'EFS, Delete', 'day'),  # busted -- MUST NOT count
]

CLEAN_DAY = 585.0
CLEAN_NIGHT = 200.0
CLEAN_FULL = CLEAN_DAY + CLEAN_NIGHT      # 785
CANCELLED = 99.0
ALL_IN_FULL = CLEAN_FULL + CANCELLED      # 884


def _load(db, ticks=TICKS, ice='CTZ6'):
    rows = []
    for i, (ts, size, cond, win) in enumerate(ticks):
        rows.append((CMD, SESS, ice, 'CTDEC1', ts, 77.0, size,
                     primary_type(tokenize(cond)), cond, 1000 + i, win,
                     '2026-07-02T00:00:00'))
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


class TestClassifierIsUnchanged:
    """The data layer was already correct. Guard it against a well-meaning fix."""

    def test_delete_still_classifies_as_efs_delete(self):
        """The historic name is preserved: stored rows stay valid."""
        assert primary_type(tokenize('EFS, Delete')) == 'efs_delete' 

    def test_a_cancelled_print_keeps_its_type_identity(self):
        """Delete is orthogonal to trade type -- a cancelled block is a
        cancelled BLOCK, not a generic bust. This is what makes the exclusion
        expressible in minute_agg/bar5m, which key on primary_type alone."""
        assert primary_type(tokenize('BlockTrde, Delete')) == 'block_delete'
        assert primary_type(tokenize('BlockTrde, Leg, Delete')) == 'block_delete'
        assert primary_type(tokenize('EFP, Delete')) == 'efp_delete'
        assert primary_type(tokenize('Leg, Delete')) == 'leg_delete'
        assert primary_type(tokenize('SetByBid, Delete')) == 'outright_delete'

    def test_plain_efs_and_efp_are_not_cancelled(self):
        assert primary_type(tokenize('EFS')) == 'efs'
        assert primary_type(tokenize('EFP')) == 'efp'
        assert not is_excluded('efs')
        assert not is_excluded('efp')

    def test_every_cancelled_bucket_is_excluded_not_just_efs(self):
        """WIDENED 2026-08-24: R11 keys on the Delete TAG, not a bucket name.

        Asserted ('efs_delete',) until the tag ruling. That narrow rule let 340
        cancelled lots keep counting because they were blocks, an EFP and a leg
        rather than EFSs."""
        assert set(EXCLUDED_FROM_CLEAN) == {
            'efs_delete', 'efp_delete', 'block_delete',
            'leg_delete', 'outright_delete', 'other_delete'}
        for t in EXCLUDED_FROM_CLEAN:
            assert is_excluded(t), f'{t} is cancelled flow and must not count'
        for t in ('outright', 'leg', 'efs', 'efp', 'block', 'other'):
            assert not is_excluded(t), f'{t} must still count'


class TestGuardFires:
    """Direction 1: a busted print must never reach a default total."""

    def test_window_sum_clean_excludes_cancelled(self, db):
        w = repo.window_sum(db, CMD, NIGHT_START, DAY_END)
        assert w['all'] == ALL_IN_FULL
        assert w['clean'] == CLEAN_FULL

    def test_rollup_window_sums_excludes_cancelled(self, db):
        per = _window_sums(db, CMD, SESS)['CTZ6']
        assert per['day'] == CLEAN_DAY
        assert per['night'] == CLEAN_NIGHT
        assert per['full'] == CLEAN_FULL

    def test_traded_contracts_total_is_clean(self, db):
        z = next(c for c in repo.traded_contracts(db, CMD, SESS)
                 if c['ice_code'] == 'CTZ6')
        assert z['total'] == CLEAN_FULL

    def test_profile_default_excludes_cancelled(self, db):
        bars = repo.profile(db, CMD, NIGHT_START, DAY_END, 'full')
        assert sum(b['sum_size'] for b in bars) == CLEAN_FULL

    def test_the_cancelled_lots_never_silently_vanish(self, db):
        """P6.6: excluded volume stays counted and attributable."""
        w = repo.window_sum(db, CMD, NIGHT_START, DAY_END)
        assert w['excluded'] == CANCELLED
        assert w['excluded_by_type'] == {'efs_delete': CANCELLED}
        assert w['clean'] + w['excluded'] == w['all']
        assert _window_sums(db, CMD, SESS)['CTZ6']['excluded'] == CANCELLED


class TestGuardDoesNotOverFire:
    """Direction 2: ICE counts plain EFS and EFP, so we must too."""

    def test_plain_efs_and_efp_survive_every_default_path(self, db):
        w = repo.window_sum(db, CMD, NIGHT_START, DAY_END)
        assert w['by_type']['efs'] == 17.0
        assert w['by_type']['efp'] == 8.0
        # and they are inside the clean total, not merely reported beside it
        assert repo.window_sum(db, CMD, NIGHT_START, DAY_END,
                               types=['efs'])['clean'] == 17.0
        assert repo.window_sum(db, CMD, NIGHT_START, DAY_END,
                               types=['efp'])['clean'] == 8.0

    def test_untagging_delete_puts_the_lots_straight_back(self, tmp_db):
        """The 99 lots are excluded for being CANCELLED, not for being EFS."""
        untagged = [(ts, size, ('EFS' if 'Delete' in cond else cond), win)
                    for ts, size, cond, win in TICKS]
        db = _load(tmp_db, ticks=untagged)
        w = repo.window_sum(db, CMD, NIGHT_START, DAY_END)
        assert w['excluded'] == 0.0
        assert w['clean'] == ALL_IN_FULL
        assert w['by_type']['efs'] == 17.0 + CANCELLED

    def test_explicit_request_for_cancelled_still_returns_it(self, db):
        """Default clean, explicit dirty -- retrievable on request."""
        w = repo.window_sum(db, CMD, NIGHT_START, DAY_END,
                            types=['efs_delete'])
        assert w['all'] == CANCELLED
        assert w['by_type'] == {'efs_delete': CANCELLED}
        bars = repo.profile(db, CMD, NIGHT_START, DAY_END, 'full',
                            types=['efs_delete'])
        assert sum(b['sum_size'] for b in bars) == CANCELLED


class TestOneRulingOneMechanism:
    """Addition 1: the rule has exactly one home. No copied predicates."""

    def test_no_second_exclusion_predicate_in_the_tree(self):
        """No module may re-implement the EXCLUSION. Naming the type is fine.

        Two legitimate uses of the literal exist and must keep working:
          * ingest/bbg_map.py -- PRODUCES the type from Bloomberg codes.
          * api/routes_query.py /catalog -- ADVERTISES it as queryable, which
            is what keeps cancelled volume retrievable on explicit request.
        What is banned is a second copy of the RULE: a comparison or SQL
        predicate that decides, locally, that efs_delete does not count.
        """
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        # '!= efs_delete', 'NOT IN (efs_delete...)', "- by_type['efs_delete']",
        # 'primary_type == efs_delete' used as a skip, etc.
        predicate = re.compile(
            r"""(!=\s*['"]efs_delete['"])"""
            r"""|(['"]efs_delete['"]\s*!=)"""
            r"""|(NOT\s+IN\s*\([^)]*efs_delete)"""
            r"""|(<>\s*['"]efs_delete['"])"""
            r"""|(-\s*by_type\.get\(\s*['"]efs_delete['"])"""
            r"""|(-\s*\w*\[\s*['"]efs_delete['"]\s*\])""",
            re.IGNORECASE)
        offenders = []
        for py in root.rglob('*.py'):
            if 'tests' in py.parts or '__pycache__' in py.parts:
                continue
            if py.name == 'classifier.py':      # the one legitimate home
                continue
            for lineno, line in enumerate(
                    py.read_text(encoding='utf-8').splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                if predicate.search(line):
                    offenders.append(f'{py.relative_to(root)}:{lineno}')
        assert not offenders, (
            'a second copy of the R11 exclusion rule exists -- import '
            f'EXCLUDED_FROM_CLEAN / excluded_sql() instead. Found: {offenders}')

    def test_clean_split_conserves_every_lot(self):
        by_type = {'outright': 13247.0, 'leg': 4766.0, 'efs': 207.0,
                   'efs_delete': 99.0, 'efp': 47.0}
        clean, excluded, by = clean_split(by_type)
        assert clean + excluded == sum(by_type.values())
        assert excluded == 99.0
        assert by == {'efs_delete': 99.0}
