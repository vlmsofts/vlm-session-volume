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
    tas_symbol,
    to_generic,
    underlying_ice_code,
)


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
