"""TAS (Trade At Settlement) volume tests.

ICE captures TAS on a symbol one letter longer than the outright -- CT's TAS
trades as 'CTZ' ('CTZ Z26'), in its own blotter file
(futures_blotter_CTZ_<FWD>_<date>.csv) sitting alongside the outright's
(futures_blotter_CT_<FWD>_<date>.csv) in the same day-folder. This was never
read by any ingest path before 2026-09-21: find_blotter_files's pattern
requires the literal filename to end in `_{cmd}_`, which a `{cmd}Z_` file
never matches -- the files were invisible, not filtered.

Lou's ruling 2026-09-21: TAS is a VOLUME-ONLY signal (it trades at settle +/-
a differential by definition, so its price carries no information and its
Conditions/aggressor axis is not meaningful the way it is for an outright
fill). Design: one 'tas' primary_type bucket, checked first in the
classification ladder ahead of Leg/EFS/EFP/Block, keyed on which blotter
file/symbol the row came from -- never on Conditions.
"""

import pytest

from ingest.blotter_parser import RawTick
from ingest.classifier import (
    EXCLUDED_FROM_CLEAN,
    clean_split,
    is_excluded,
    primary_type,
    tokenize,
)
from ingest.normalize import (
    is_tas_ice_code,
    normalize_contract,
    normalize_tick,
    tas_ice_code_for,
    tas_symbol,
    to_generic,
    underlying_ice_code,
)
from store import repository as repo


# ---------------------------------------------------------------------------
# Symbol identification -- the part that is easy to get subtly wrong
# ---------------------------------------------------------------------------

class TestTasSymbolIdentification:

    def test_tas_contract_normalizes_with_doubled_letter(self):
        assert normalize_contract('CTZ Z26') == 'CTZZ6'
        assert normalize_contract('CT Z26') == 'CTZ6'

    def test_is_tas_by_length_not_by_prefix(self):
        """THE BUG THIS GUARDS: a naive prefix check ('starts with CTZ') calls
        the real December OUTRIGHT 'CTZ6' a TAS contract, because 'CTZ6'
        legitimately starts with the same three characters a TAS-prefix test
        would use. Caught during this feature's own build before it shipped:
        underlying_ice_code('CTZ6', 'CT') returned 'CT6' on the first cut.
        The correct discriminator is LENGTH -- TAS always carries exactly one
        extra character versus an outright of the same length shape."""
        assert is_tas_ice_code('CTZZ6', 'CT') is True
        assert is_tas_ice_code('CTZ6', 'CT') is False    # the real Dec outright
        assert is_tas_ice_code('CTZ7', 'CT') is False    # Dec27 outright
        assert is_tas_ice_code('CTH7', 'CT') is False    # unrelated month

    def test_is_tas_never_guesses_for_an_unconfigured_commodity(self):
        """No commodity may be assumed to have a TAS symbol; it must be
        confirmed on disk first (same discipline as expiry_source.py's
        vendored historical FNDs)."""
        assert is_tas_ice_code('KCU6', 'KC') is False
        assert tas_symbol('KC') is None
        assert tas_symbol('CT') == 'CTZ'

    def test_underlying_ice_code_unwraps_tas_and_passes_through_outright(self):
        assert underlying_ice_code('CTZZ6', 'CT') == 'CTZ6'
        assert underlying_ice_code('CTZ6', 'CT') == 'CTZ6'   # unchanged, not mangled
        assert underlying_ice_code('CTH7', 'CT') == 'CTH7'


# ---------------------------------------------------------------------------
# Classification -- TAS wins over Conditions, but not over Delete
# ---------------------------------------------------------------------------

class TestTasClassification:

    def test_tas_bucket_ignores_conditions(self):
        """Per Lou's ruling: a TAS print carrying Leg, or no aggressor stamp
        at all, is still 'tas' -- the Conditions axis is not meaningful for a
        settlement-differential trade the way it is for an outright fill."""
        assert primary_type(tokenize(''), is_tas=True) == 'tas'
        assert primary_type(tokenize('SetByAsk'), is_tas=True) == 'tas'
        assert primary_type(tokenize('Leg'), is_tas=True) == 'tas'

    def test_non_tas_rows_are_unaffected(self):
        assert primary_type(tokenize('Leg'), is_tas=False) == 'leg'
        assert primary_type(tokenize('Leg')) == 'leg'   # default arg unchanged

    def test_cancelled_tas_excludes_from_clean_total(self):
        """Not yet observed on the tape, but if ICE ever busts a TAS print it
        must obey R11 exactly like every other bucket: the row is KEPT, but
        never summed into a default total."""
        p = primary_type(tokenize('Delete'), is_tas=True)
        assert p == 'tas_delete'
        assert is_excluded(p)
        assert 'tas_delete' in EXCLUDED_FROM_CLEAN

    def test_clean_split_counts_tas_but_not_cancelled_tas(self):
        clean, excluded, excluded_by = clean_split(
            {'tas': 100, 'tas_delete': 5, 'outright': 50})
        assert clean == 150
        assert excluded == 5
        assert excluded_by == {'tas_delete': 5}


# ---------------------------------------------------------------------------
# End-to-end: normalize_tick derives everything from ice_code alone
# ---------------------------------------------------------------------------

class TestTasNormalizeTick:

    def test_tas_row_gets_distinct_ice_code_and_rolled_up_generic(self):
        """ice_code must be DISTINCT from the outright's (CTZZ6 vs CTZ6) so a
        PK collision on (commodity, session_date, ice_code, seq_num) is
        structurally impossible -- not merely unobserved. generic_code must
        still resolve to the underlying contract's slot (CTDEC1) so TAS
        volume rolls up under the same generic an outright would."""
        raw = RawTick(contract='CTZ Z26', exchange_time='2026-09-17T21:00:00',
                     price=0.0, size=9.0, conditions='SetByAsk', seq_num=20033)
        nt = normalize_tick(raw, 'CT', '2026-09-18')
        assert nt.ice_code == 'CTZZ6'
        assert nt.generic_code == 'CTDEC1'
        assert nt.primary_type == 'tas'

    def test_outright_row_is_unaffected_by_tas_logic_existing(self):
        raw = RawTick(contract='CT Z26', exchange_time='2026-09-17T21:00:00',
                     price=82.25, size=1.0, conditions='SetByAsk', seq_num=33851)
        nt = normalize_tick(raw, 'CT', '2026-09-18')
        assert nt.ice_code == 'CTZ6'
        assert nt.generic_code == 'CTDEC1'
        assert nt.primary_type == 'outright'

    def test_tas_and_outright_of_the_same_month_never_collide_on_ice_code(self):
        tas = normalize_tick(
            RawTick(contract='CTZ Z26', exchange_time='2026-09-17T21:00:00',
                    price=0.0, size=1.0, conditions='', seq_num=1),
            'CT', '2026-09-18')
        out = normalize_tick(
            RawTick(contract='CT Z26', exchange_time='2026-09-17T21:00:00',
                    price=82.0, size=1.0, conditions='', seq_num=1),
            'CT', '2026-09-18')
        # Same seq_num deliberately (ICE's TAS and outright feeds are
        # independently sequenced) -- if ice_code ever collided too, this
        # would be a silent PK conflict via ON CONFLICT DO NOTHING.
        assert tas.ice_code != out.ice_code

    def test_tas_with_leg_condition_still_classifies_as_tas(self):
        raw = RawTick(contract='CTZ Z26', exchange_time='2026-09-17T21:00:00',
                     price=0.0, size=5.0, conditions='Leg', seq_num=20034)
        nt = normalize_tick(raw, 'CT', '2026-09-18')
        assert nt.primary_type == 'tas'   # not 'leg'


# ---------------------------------------------------------------------------
# to_generic: the resolver call must use the underlying code, not CTZZ6
# ---------------------------------------------------------------------------

def test_to_generic_unwraps_tas_before_resolving():
    """The resolver has never heard of 'CTZZ6' -- it must be unwrapped to
    'CTZ6' before ice_to_generic is called, or this silently returns None for
    every TAS row (the same failure class as the pre-2026-07-31 KC/CC/SB
    null-generic bug)."""
    assert to_generic('CTZZ6', '2026-09-18', 'CT') == 'CTDEC1'
    assert to_generic('CTZ6', '2026-09-18', 'CT') == 'CTDEC1'   # unaffected


# ---------------------------------------------------------------------------
# tas_ice_code_for: the inverse mapping the contracts picker fix depends on
# ---------------------------------------------------------------------------
#
# BUG FOUND 2026-09-21 BY LOU, SAME DAY THIS FEATURE SHIPPED: the dashboard's
# contracts dropdown lists outright ice_codes only (CTZ6, CTH7, ...), and
# _contract_filter matched on ice_code/generic_code exactly as given. Picking
# CTZ6 -- the obvious, natural choice -- therefore matched ZERO CTZZ6 rows,
# so a user with the TAS checkbox CHECKED still saw no TAS volume unless they
# separately picked the unlabeled CTZZ6 row. "You have to pick CTZZ6 to get
# TAS...sloppy." Fixed by expanding an outright pick to also match its TAS
# twin in the SQL filter (tas_ice_code_for), and by removing CTZZ6 from the
# picker's own list entirely (traded_contracts) so it never appears as a
# confusing, unlabeled second row next to the outright it belongs to.

class TestTasIceCodeFor:

    def test_outright_maps_to_its_tas_twin(self):
        assert tas_ice_code_for('CTZ6', 'CT') == 'CTZZ6'
        assert tas_ice_code_for('CTH7', 'CT') == 'CTZH7'

    def test_no_tas_of_a_tas(self):
        assert tas_ice_code_for('CTZZ6', 'CT') is None

    def test_none_for_a_commodity_with_no_confirmed_tas_symbol(self):
        assert tas_ice_code_for('KCU6', 'KC') is None

    def test_round_trips_with_underlying_ice_code(self):
        for code in ('CTZ6', 'CTH7', 'CTK7', 'CTN7'):
            assert underlying_ice_code(tas_ice_code_for(code, 'CT'), 'CT') == code


# ---------------------------------------------------------------------------
# END-TO-END: picking the outright in the contracts filter must include TAS
# ---------------------------------------------------------------------------
#
# Reproduces the exact bug against a real database, not just the helper
# functions above -- Lou's report was about what the dashboard's contract
# picker + TAS checkbox actually produce together via window_sum/
# traded_contracts, which is a different code path from is_tas_ice_code/
# tas_ice_code_for in isolation.

SESS = '2026-09-18'
CMD = 'CT'


def _load_outright_and_tas(db):
    from ingest.aggregator import rebuild_minute_agg
    rows = [
        (CMD, SESS, 'CTZ6', 'CTDEC1', f'{SESS}T09:00:00', 82.0, 100.0,
         'outright', 'SetByAsk', 1, 'day', f'{SESS}T00:00:00'),
        (CMD, SESS, 'CTZZ6', 'CTDEC1', f'{SESS}T09:01:00', 0.0, 50.0,
         'tas', 'SetByAsk', 2, 'day', f'{SESS}T00:00:00'),
        (CMD, SESS, 'CTH7', 'CTMAR1', f'{SESS}T09:02:00', 83.0, 30.0,
         'outright', 'SetByBid', 3, 'day', f'{SESS}T00:00:00'),
    ]
    db.execmany(
        'INSERT INTO ticks (commodity, session_date, ice_code, generic_code,'
        ' exchange_time, price, size, primary_type, conditions_raw, seq_num,'
        ' window_preset, ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        rows)
    db.commit()
    rebuild_minute_agg(db, CMD, SESS)
    return db


@pytest.fixture
def tas_db(tmp_db):
    return _load_outright_and_tas(tmp_db)


class TestContractPickerIncludesTas:
    """THE BUG, reproduced end-to-end. Lou: 'so you have a tas button but the
    individual symbol also...so ctz6 selected all other boxes except tas
    work...you have to pick ctzz6 to get tas...sloppy.'"""

    def test_picking_the_outright_alone_still_includes_its_tas_volume(self, tas_db):
        """This is the exact scenario Lou hit: contracts=['CTZ6'] (the
        natural, obvious pick), types includes 'tas'. Before the fix,
        _contract_filter matched ice_code/generic_code EXACTLY as given, so
        'CTZZ6' never matched and this returned 0 TAS lots despite the
        checkbox being on."""
        r = repo.window_sum(tas_db, CMD, f'{SESS}T00:00:00', f'{SESS}T23:59:59',
                           contracts=['CTZ6'],
                           types=['outright', 'tas'])
        assert r['by_type'].get('tas') == 50.0, (
            'picking the outright must still surface its TAS volume when '
            'the tas type is requested')
        assert r['by_type'].get('outright') == 100.0
        # CTH7's 30 lots must NOT leak in -- the contract scope still narrows.
        assert 'CTH7' not in r['by_contract']

    def test_unchecking_tas_still_excludes_it_even_with_the_outright_picked(self, tas_db):
        """The types= filter must remain the ONLY thing deciding whether TAS
        counts -- the contract-scope fix must not force TAS in unconditionally."""
        r = repo.window_sum(tas_db, CMD, f'{SESS}T00:00:00', f'{SESS}T23:59:59',
                           contracts=['CTZ6'],
                           types=['outright'])
        assert 'tas' not in r['by_type']
        assert r['clean'] == 100.0

    def test_no_contract_filter_is_unaffected(self, tas_db):
        """The all-contracts default (no picker selection) must keep working
        exactly as before -- this fix only changes behavior when a SPECIFIC
        contract is chosen."""
        r = repo.window_sum(tas_db, CMD, f'{SESS}T00:00:00', f'{SESS}T23:59:59',
                           types=['outright', 'tas'])
        assert r['by_type']['tas'] == 50.0
        assert r['by_type']['outright'] == 130.0   # CTZ6 + CTH7

    def test_traded_contracts_never_lists_tas_as_its_own_row(self, tas_db):
        """CTZZ6 must not appear in the contracts picker's own list -- it
        would show as an unlabeled, confusing duplicate of CTZ6."""
        rows = repo.traded_contracts(tas_db, CMD, SESS)
        codes = [r['ice_code'] for r in rows]
        assert 'CTZZ6' not in codes
        assert 'CTZ6' in codes and 'CTH7' in codes
