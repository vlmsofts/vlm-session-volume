"""
AGGRESSOR SIDE -- sabotage suite, both directions.

  SetByBid = BUY.  SetByAsk = SELL.  ICE ticket 0903465452, 25-day scan.

The most inversion-prone fact in the stack, and its failure mode is SILENT:
flipping it changes the sign of every signed-flow number while the distribution
still looks entirely plausible. Two ad-hoc scripts inverted it in one evening by
trusting a stale doc. These tests exist so that cannot happen quietly again.

FIRES              inverting the map in aggressor.py turns these red.
DOES NOT OVER-FIRE leg / efs / efp / block / every cancelled type stay
                   'unsided', and a side-unaware query returns exactly what it
                   returned before side existed.
CONSERVATION       buy + sell + unsided == the outright total, at every grain.

Synthetic ticks only -- no production path, no real blotter, no ICE call.
"""

import pytest

from ingest.aggressor import (BUY, SELL, SIDES, UNSIDED, is_sided, side_for,
                              side_for_conditions, unexpected_tokens)
from ingest.classifier import primary_type, tokenize
from store import repository as repo

CMD = 'CT'
SESS = '2026-07-02'
START = '2026-07-01T21:00:00'
END = f'{SESS}T14:20:00'

# Distinct primes-ish sizes so a wrong bucket shows as a unique number.
TICKS = [
    (f'{SESS}T09:00:00', 500.0, 'SetByBid'),        # outright BUY
    (f'{SESS}T09:00:30', 300.0, 'SetByAsk'),        # outright SELL
    (f'{SESS}T09:01:00', 70.0, ''),                 # outright, NO stamp
    (f'{SESS}T09:01:10', 60.0, 'Leg'),              # leg      -> unsided
    (f'{SESS}T09:01:30', 17.0, 'EFS'),              # efs      -> unsided
    (f'{SESS}T09:01:45', 8.0, 'EFP'),               # efp      -> unsided
    (f'{SESS}T09:01:50', 40.0, 'BlockTrde, Leg'),   # block    -> unsided
    (f'{SESS}T09:02:31', 99.0, 'EFS, Delete'),      # cancelled-> unsided
    (f'{SESS}T09:03:10', 25.0, 'BlockTrde, Delete'),  # cancelled-> unsided
]

BUY_LOTS = 500.0
SELL_LOTS = 300.0
OUTRIGHT_UNSIDED = 70.0
OUTRIGHT_TOTAL = BUY_LOTS + SELL_LOTS + OUTRIGHT_UNSIDED     # 870
NON_OUTRIGHT_CLEAN = 60.0 + 17.0 + 8.0 + 40.0                # 125
CANCELLED = 99.0 + 25.0                                       # 124
CLEAN_TOTAL = OUTRIGHT_TOTAL + NON_OUTRIGHT_CLEAN             # 995


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


class TestTheMappingItself:
    """Direction 1: the pinned rule, asserted head-on."""

    def test_setbybid_is_buy_and_setbyask_is_sell(self):
        """ICE ticket 0903465452. If this fails, the authority is a written ICE
        confirmation -- NOT a WebICE colour, NOT intuition, NOT a stale doc."""
        assert side_for_conditions('outright', 'SetByBid') == BUY
        assert side_for_conditions('outright', 'SetByAsk') == SELL

    def test_an_unstamped_print_is_never_guessed(self):
        assert side_for_conditions('outright', '') == UNSIDED
        assert side_for_conditions('outright', None) == UNSIDED

    def test_exactly_three_states_and_unsided_is_not_a_side(self):
        assert SIDES == ('buy', 'sell', 'unsided')
        assert is_sided(BUY) and is_sided(SELL)
        assert not is_sided(UNSIDED), 'unsided is the absence of a side'

    def test_a_compound_stamp_still_resolves(self):
        """'SetByBid, Leg' is a LEG by the ladder, so it is unsided -- the
        aggressor tag does not promote a structure member to an outright."""
        assert primary_type(tokenize('SetByBid, Leg')) == 'leg'
        assert side_for('leg', tokenize('SetByBid, Leg')) == UNSIDED


class TestDoesNotOverFire:
    """Direction 2: only live outrights carry a side. Asserted, not assumed."""

    @pytest.mark.parametrize('conditions,expected_type', [
        ('Leg', 'leg'), ('EFS', 'efs'), ('EFP', 'efp'),
        ('BlockTrde, Leg', 'block'), ('EFS, Delete', 'efs_delete'),
        ('BlockTrde, Delete', 'block_delete'), ('EFP, Delete', 'efp_delete'),
        ('Leg, Delete', 'leg_delete'),
    ])
    def test_non_outright_types_are_unsided_by_construction(
            self, conditions, expected_type):
        ptype = primary_type(tokenize(conditions))
        assert ptype == expected_type
        assert side_for(ptype, tokenize(conditions)) == UNSIDED

    def test_a_cancelled_outright_is_unsided_not_signed(self):
        """R11 keeps busted prints out of every default. Reporting a side for
        one would invite it back into a signed sum."""
        ptype = primary_type(tokenize('SetByBid, Delete'))
        assert ptype == 'outright_delete'
        assert side_for(ptype, tokenize('SetByBid, Delete')) == UNSIDED


class TestTheArchiveCarriesSide:
    """side is a real second axis in minute_agg, additive to primary_type."""

    def test_minute_agg_splits_outright_by_side(self, db):
        rows = dict(db.q(
            "SELECT side, SUM(sum_size) FROM minute_agg"
            " WHERE commodity=%s AND session_date=%s AND primary_type='outright'"
            ' GROUP BY side', (CMD, SESS)))
        assert rows == {BUY: BUY_LOTS, SELL: SELL_LOTS,
                        UNSIDED: OUTRIGHT_UNSIDED}

    def test_primary_type_is_untouched_by_the_new_axis(self, db):
        """ADDITIVE: outright is still one primary_type, not three."""
        rows = dict(db.q(
            'SELECT primary_type, SUM(sum_size) FROM minute_agg'
            ' WHERE commodity=%s AND session_date=%s GROUP BY primary_type',
            (CMD, SESS)))
        assert rows['outright'] == OUTRIGHT_TOTAL
        assert rows['efs_delete'] == 99.0
        assert rows['block_delete'] == 25.0

    def test_every_non_outright_row_is_unsided_in_the_archive(self, db):
        bad = db.q(
            'SELECT primary_type, side FROM minute_agg'
            " WHERE commodity=%s AND session_date=%s"
            " AND primary_type <> 'outright' AND side <> 'unsided'",
            (CMD, SESS))
        assert bad == [], f'non-outright rows carrying a side: {bad}'

    def test_bar5m_carries_side_through_unchanged(self, db):
        from ingest.bar5m import rollup_ice_bar5m
        rollup_ice_bar5m(db, CMD, SESS)
        rows = dict(db.q(
            "SELECT side, SUM(sum_size) FROM bar5m WHERE source='ice'"
            " AND commodity=%s AND session_date=%s AND primary_type='outright'"
            ' GROUP BY side', (CMD, SESS)))
        assert rows == {BUY: BUY_LOTS, SELL: SELL_LOTS,
                        UNSIDED: OUTRIGHT_UNSIDED}


class TestConservation:
    """buy + sell + unsided == the outright total, at every grain."""

    def test_conservation_in_minute_agg(self, db):
        total = db.q(
            'SELECT SUM(sum_size) FROM minute_agg WHERE commodity=%s'
            " AND session_date=%s AND primary_type='outright'",
            (CMD, SESS))[0][0]
        parts = dict(db.q(
            'SELECT side, SUM(sum_size) FROM minute_agg WHERE commodity=%s'
            " AND session_date=%s AND primary_type='outright' GROUP BY side",
            (CMD, SESS)))
        assert sum(parts.values()) == total == OUTRIGHT_TOTAL

    def test_conservation_in_bar5m(self, db):
        from ingest.bar5m import rollup_ice_bar5m
        rollup_ice_bar5m(db, CMD, SESS)
        total = db.q(
            "SELECT SUM(sum_size) FROM bar5m WHERE source='ice' AND"
            " commodity=%s AND session_date=%s AND primary_type='outright'",
            (CMD, SESS))[0][0]
        parts = dict(db.q(
            "SELECT side, SUM(sum_size) FROM bar5m WHERE source='ice' AND"
            " commodity=%s AND session_date=%s AND primary_type='outright'"
            ' GROUP BY side', (CMD, SESS)))
        assert sum(parts.values()) == total == OUTRIGHT_TOTAL

    def test_the_whole_day_still_conserves_across_both_axes(self, db):
        grand = db.q('SELECT SUM(sum_size) FROM minute_agg WHERE commodity=%s'
                     ' AND session_date=%s', (CMD, SESS))[0][0]
        assert grand == CLEAN_TOTAL + CANCELLED


class TestExistingCallersUnchanged:
    """Item 6: a query that does not ask for side returns what it always did."""

    def test_window_sum_is_unchanged_by_the_new_axis(self, db):
        w = repo.window_sum(db, CMD, START, END)
        assert w['clean'] == CLEAN_TOTAL
        assert w['excluded'] == CANCELLED
        assert w['all'] == CLEAN_TOTAL + CANCELLED
        assert w['by_type']['outright'] == OUTRIGHT_TOTAL

    def test_traded_contracts_is_unchanged(self, db):
        z = next(c for c in repo.traded_contracts(db, CMD, SESS)
                 if c['ice_code'] == 'CTZ6')
        assert z['total'] == CLEAN_TOTAL

    def test_profile_is_unchanged(self, db):
        bars = repo.profile(db, CMD, START, END, 'full')
        assert sum(b['sum_size'] for b in bars) == CLEAN_TOTAL


class TestUnexpectedTokensAreFlagged:
    """A new ICE token must surface, never be absorbed into unsided unseen."""

    def test_a_known_vocabulary_flags_nothing(self):
        for c in ('SetByBid', 'SetByAsk', 'Leg', 'EFS', 'EFP',
                  'BlockTrde, Leg', 'EFS, Delete', ''):
            assert unexpected_tokens(tokenize(c)) == frozenset()

    def test_an_unknown_token_is_flagged(self):
        assert unexpected_tokens(tokenize('Wibble')) == frozenset({'Wibble'})
        # RFCCross is options-only: unsided like anything unstamped, and
        # flagged so its arrival on a futures tape would be noticed.
        assert unexpected_tokens(tokenize('RFCCross')) == frozenset({'RFCCross'})
        assert side_for_conditions('outright', 'RFCCross') == UNSIDED


class TestSideProfile:
    """repo.side_profile -- the aggressor time series view's query.

    Same base as session_render.aggressor_split (aggressor-tagged outrights
    only), but window-bounded and source-aware like window_sum/profile, so a
    range of session dates can be queried one call per date the same way the
    dashboard's other range views already do.
    """

    def test_matches_aggressor_split_on_the_full_window(self, db):
        out = repo.side_profile(db, CMD, START, END, session_date=SESS)
        assert out['base_lots'] == BUY_LOTS + SELL_LOTS
        assert out['buy']['lots'] == BUY_LOTS
        assert out['sell']['lots'] == SELL_LOTS
        assert out['unsided']['lots'] == OUTRIGHT_UNSIDED
        assert out['outright_total'] == OUTRIGHT_TOTAL

    def test_conservation_buy_sell_unsided_equals_outright_total(self, db):
        out = repo.side_profile(db, CMD, START, END, session_date=SESS)
        assert abs(out['buy']['lots'] + out['sell']['lots']
                   + out['unsided']['lots'] - out['outright_total']) < 0.0001

    def test_clip_is_lots_per_print(self, db):
        out = repo.side_profile(db, CMD, START, END, session_date=SESS)
        assert out['buy']['clip'] == BUY_LOTS / 1        # one BUY print
        assert out['sell']['clip'] == SELL_LOTS / 1       # one SELL print

    def test_unsided_pct_of_base_is_never_expressed(self, db):
        out = repo.side_profile(db, CMD, START, END, session_date=SESS)
        assert out['unsided']['pct_of_base'] is None

    def test_a_window_with_no_rows_returns_honest_zeros_not_an_error(self, db):
        out = repo.side_profile(db, CMD, '1999-01-01T00:00:00', '1999-01-02T00:00:00',
                                session_date='1999-01-01')
        assert out == {
            'base_lots': 0.0,
            'buy': {'lots': 0.0, 'prints': 0, 'pct_of_base': None, 'clip': None},
            'sell': {'lots': 0.0, 'prints': 0, 'pct_of_base': None, 'clip': None},
            'unsided': {'lots': 0.0, 'prints': 0, 'pct_of_base': None, 'clip': None},
            'outright_total': 0.0,
        }

    def test_bar5m_bloomberg_source_is_honest_all_unsided(self, db):
        """The Bloomberg seed writes side='unsided' for every row (no
        aggressor was ever captured for that era) -- this must come back as a
        stated 100% unsided, never a fabricated split and never hidden."""
        from ingest.bar5m import replace_bloomberg_day
        replace_bloomberg_day(db, CMD, SESS, 'CTZ6', 'CTDEC1',
                              {('2026-07-01T21:00', 'outright'): (OUTRIGHT_TOTAL, 3)})
        out = repo.side_profile(db, CMD, START, END, session_date=SESS,
                                source='bloomberg')
        assert out['base_lots'] == 0.0
        assert out['buy']['lots'] == 0.0
        assert out['sell']['lots'] == 0.0
        assert out['unsided']['lots'] == OUTRIGHT_TOTAL
        assert out['buy']['pct_of_base'] is None
        assert out['sell']['pct_of_base'] is None


class TestOneRulingOneMechanism:
    """No second copy of the side rule anywhere in the tree."""

    def test_no_second_side_predicate_in_the_tree(self):
        """Naming the tokens is fine -- classifier owns the TOKEN test and is
        the one legitimate home. What is banned is a second copy of the
        TAG-TO-SIDE decision: any module mapping SetByBid/SetByAsk to a
        buy/sell string on its own. That is how an inversion gets introduced
        in one place and missed everywhere else.
        """
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        # MULTILINE by necessity. The realistic shape of a hand-rolled second
        # mapping is an if/return spanning lines:
        #     if 'SetByBid' in cond:
        #         return 'buy'
        # A single-line regex misses exactly that -- proven by sabotage, which
        # is why this test was widened. Comment lines are blanked first so
        # naming the tokens in prose cannot trip it.
        predicate = re.compile(
            r"""SetBy(?:Bid|Ask)(?:.|\n){0,120}?['"](?:buy|sell)['"]"""
            r"""|['"](?:buy|sell)['"](?:.|\n){0,120}?SetBy(?:Bid|Ask)""",
            re.IGNORECASE)
        comment_start = ('#', '"""', "'''")
        offenders = []
        for py in root.rglob('*.py'):
            if 'tests' in py.parts or '__pycache__' in py.parts:
                continue
            if py.name == 'aggressor.py':      # the one legitimate home
                continue
            lines = py.read_text(encoding='utf-8').splitlines()
            blanked = ['' if ln.lstrip().startswith(comment_start) else ln
                       for ln in lines]
            body = '\n'.join(blanked)
            for m in predicate.finditer(body):
                lineno = body[:m.start()].count('\n') + 1
                offenders.append(f'{py.relative_to(root)}:{lineno}')
        assert not offenders, (
            'a second copy of the aggressor mapping exists -- import from '
            f'ingest.aggressor instead. Found: {offenders}')
