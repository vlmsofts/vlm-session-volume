"""
SESSION CARD FIGURES -- the numbers that reach a client page.

Every figure on the card must be reproducible from the store and must carry the
basis it was measured on. These tests pin the partitions and the definitions.

Two conservation laws, both asserted in code as well as here, because a
partition that stops summing is a defect that surfaces as a quietly wrong
number rather than an error:

    spread legs + option hedges == all leg prints, and all leg lots
    buy + sell + unsided        == the outright total

Synthetic ticks only. No production path, no real blotter, no ICE call.
"""

import pytest

from ingest.aggressor import BUY, SELL, UNSIDED
from ingest.classifier import primary_type, tokenize
from store import session_render as sr

CMD = 'CT'
SESS = '2026-07-02'

# Two same-second, same-size leg pairs across contracts -> 4 spread legs.
# One lone big leg -> 1 option hedge (the 312-at-88.00 shape).
# One same-second pair of DIFFERENT sizes -> both option hedges: no size match.
TICKS = [
    # (ice, time, size, conditions)
    ('CTZ6', f'{SESS}T09:00:00', 10.0, 'Leg'),
    ('CTH7', f'{SESS}T09:00:00', 10.0, 'Leg'),      # pairs with the above
    ('CTZ6', f'{SESS}T09:01:00', 25.0, 'Leg'),
    ('CTH7', f'{SESS}T09:01:00', 25.0, 'Leg'),      # pairs
    ('CTZ6', f'{SESS}T10:33:48', 312.0, 'Leg'),     # lone -> option hedge
    ('CTZ6', f'{SESS}T11:00:00', 7.0, 'Leg'),
    ('CTH7', f'{SESS}T11:00:00', 9.0, 'Leg'),       # same second, size differs
    # outrights for the aggressor picture
    ('CTZ6', f'{SESS}T09:05:00', 500.0, 'SetByBid'),
    ('CTZ6', f'{SESS}T09:06:00', 300.0, 'SetByAsk'),
    ('CTZ6', f'{SESS}T09:07:00', 70.0, ''),         # unstamped
    ('CTZ6', f'{SESS}T09:08:00', 17.0, 'EFS'),
]


def _load(db):
    rows = []
    for i, (ice, ts, size, cond) in enumerate(TICKS):
        rows.append((CMD, SESS, ice, 'CTDEC1', ts, 77.0, size,
                     primary_type(tokenize(cond)), cond, 1000 + i, 'day',
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


class TestLegSplit:
    """A spread leg has a same-second, SAME-SIZE leg partner on another
    contract. Anything else is an option hedge."""

    def test_size_matched_pairs_are_spread_legs(self, db):
        s = sr.leg_split(db, CMD, SESS)
        assert s[sr.SPREAD_LEG]['prints'] == 4
        assert s[sr.SPREAD_LEG]['lots'] == 70.0     # 10+10+25+25

    def test_a_lone_leg_is_an_option_hedge(self, db):
        s = sr.leg_split(db, CMD, SESS, 'CTZ6')
        assert s[sr.OPTION_HEDGE]['lots'] == 312.0 + 7.0

    def test_same_second_but_different_size_is_not_a_spread_leg(self, db):
        """The definition the evidence rests on is size-matched. A same-second
        pair of different sizes must NOT be counted as a spread."""
        s = sr.leg_split(db, CMD, SESS)
        # the 7 and the 9 both fall to option hedges
        assert s[sr.OPTION_HEDGE]['prints'] == 3
        assert s[sr.OPTION_HEDGE]['lots'] == 312.0 + 7.0 + 9.0

    def test_the_partition_is_exhaustive(self, db):
        """CONSERVATION. Also asserted inside leg_split itself."""
        s = sr.leg_split(db, CMD, SESS)
        assert (s[sr.SPREAD_LEG]['prints'] + s[sr.OPTION_HEDGE]['prints']
                == s['total']['prints'])
        assert (s[sr.SPREAD_LEG]['lots'] + s[sr.OPTION_HEDGE]['lots']
                == s['total']['lots'])

    def test_it_is_per_print_not_per_row_pair(self, db):
        """A JOIN would count one print matching N partners N times. The total
        print count is the guard: it can never exceed the leg prints."""
        s = sr.leg_split(db, CMD, SESS)
        n_legs = db.q("SELECT COUNT(*) FROM ticks WHERE commodity=%s AND "
                      "session_date=%s AND primary_type='leg'",
                      (CMD, SESS))[0][0]
        assert s['total']['prints'] == n_legs


class TestAggressorBase:
    """The base is aggressor-tagged outrights only."""

    def test_base_excludes_unstamped_efs_and_legs(self, db):
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert a['base_lots'] == 800.0             # 500 buy + 300 sell only
        assert a['buy']['lots'] == 500.0
        assert a['sell']['lots'] == 300.0

    def test_unstamped_is_reported_but_never_in_the_base(self, db):
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert a['unsided']['lots'] == 70.0
        assert a['unsided']['pct_of_base'] is None, (
            'an unsided share of the aggressor base would imply it is part of '
            'the base')

    def test_blanks_are_never_prorated_across_buy_and_sell(self, db):
        """The 70 unstamped lots must not appear in buy or sell."""
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert a['buy']['lots'] + a['sell']['lots'] == 800.0
        assert a['outright_total'] == 870.0

    def test_clip_size_per_side(self, db):
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert a['buy']['clip'] == 500.0
        assert a['sell']['clip'] == 300.0

    def test_conservation(self, db):
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert (a['buy']['lots'] + a['sell']['lots'] + a['unsided']['lots']
                == a['outright_total'])


class TestTypeBreakdown:

    def test_leg_is_replaced_by_its_two_populations(self, db):
        t = sr.type_breakdown(db, CMD, SESS)
        assert 'leg' not in t, 'leg must not survive as a single row'
        assert sr.SPREAD_LEG in t and sr.OPTION_HEDGE in t

    def test_blanks_stay_inside_the_outright_total(self, db):
        """Lou's ruling: blanks stay IN the total, OUT of the aggressor count."""
        t = sr.type_breakdown(db, CMD, SESS, 'CTZ6')
        assert t['outright']['lots'] == 870.0       # 500 + 300 + 70

    def test_the_named_blank_note_matches_the_unsided_outrights(self, db):
        b = sr.blank_note(db, CMD, SESS, 'CTZ6')
        a = sr.aggressor_split(db, CMD, SESS, 'CTZ6')
        assert b['lots'] == a['unsided']['lots'] == 70.0


class TestSessionWindow:

    def test_the_window_end_is_the_boundary_not_the_last_print(self, db):
        """A CT session is [21:00, 14:20). Printing the last print as the
        window end reads like a truncated capture."""
        w = sr.session_window(db, CMD, SESS)
        assert w['window_end'] == f'{SESS}T14:20'
        assert w['last_print'] < w['window_end']

    def test_the_start_is_read_from_the_tape_not_boilerplate(self, db):
        """A Sunday open or a short session must print its REAL hours."""
        w = sr.session_window(db, CMD, SESS)
        assert w['window_start'] == f'{SESS}T09:00'
