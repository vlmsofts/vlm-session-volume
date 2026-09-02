"""test_expiry_source_roll.py -- the generic board rolls at FIRST NOTICE DAY,
and the roll date always comes from an authority, never from arithmetic.

THE BUG THESE PIN (found 2026-09-02, Lou: "we do not leave broken features"):
contract_resolver.resolve_generic() rolled at `date(year, delivery_month, 1)`
while its own header comment claimed first-notice. Real CT contracts leave the
board at FND, five to eight days earlier, so the front-month generic pointed at
an already-dead contract for ~6 sessions per roll. Because ice_to_generic()
stamps generic_code into the archive (ingest/normalize.py, ingest/rollup.py),
25,237 of 195,188 stored bar5m rows sat in a disputed window.

Evidence the boundary is FND and not the 1st, all three independent:
  * Bloomberg FUT_NOTICE_FIRST for CTH26/CTK26/CTN26, cross-validated against
    the live gateway on CTZ26 and CTH27 (exact match both).
  * The archive's own volume cliff: CTN6 falls 132 -> 5 bars ON its FND.
  * Lou's shape rule (FND = 5 business days before the 1st business day of the
    delivery month) agrees on 4 of 5 checked contracts; CTZ26 shifts a day for
    Thanksgiving, which is exactly why the authority is the source and the rule
    is only a cross-check.

The dates below are NOT recomputed here -- they are asserted against what the
authority returns, so a test failure means the authority changed or the
resolver drifted, never that a formula went stale.
"""

import datetime as dt

import pytest

import expiry_source
from expiry_source import ExpiryUnavailable, first_notice_day, has_rolled
from contract_resolver import ice_to_generic, resolve_generic


# The three vendored historical contracts, with the FND/LTD Lou confirmed
# independently on 2026-09-02 (his table matched the Bloomberg pull exactly).
VENDORED = [
    ('CTH26', dt.date(2026, 2, 23)),
    ('CTK26', dt.date(2026, 4, 24)),
    ('CTN26', dt.date(2026, 6, 24)),
]


# ---------------------------------------------------------------------------
# THE AUTHORITY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('contract,expected_fnd', VENDORED)
def test_expired_contracts_still_resolve(contract, expected_fnd):
    """Neither live authority retains these (gateway serves listed contracts
    only; expiry_master.csv was snapshotted after they expired), so the
    vendored table is the only thing standing between the archive and an
    unlabelable 84,475 bars."""
    assert first_notice_day(contract) == expected_fnd


def test_unknown_contract_refuses_rather_than_guessing():
    """Protocol section 5: never derive a contract date from calendar
    arithmetic. An undatable contract must raise, not fall back to month math."""
    with pytest.raises(ExpiryUnavailable):
        first_notice_day('CTZ99')


def test_provenance_is_reported_not_asserted():
    """A status field is not evidence. The module must SAY how old each
    authority's data is so a caller can surface it, rather than silently
    presenting stale expiry as current."""
    notes = expiry_source.provenance()
    assert any('gateway' in n for n in notes)
    assert any('local' in n for n in notes)


# ---------------------------------------------------------------------------
# THE ROLL BOUNDARY -- FND, not the 1st of the delivery month
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('contract,fnd', VENDORED)
def test_contract_is_live_the_day_before_fnd(contract, fnd):
    assert has_rolled(contract, fnd - dt.timedelta(days=1)) is False


@pytest.mark.parametrize('contract,fnd', VENDORED)
def test_contract_has_rolled_on_fnd_itself(contract, fnd):
    """ON its FND, not the day after: CTN6's bar count collapses 132 -> 5 on
    2026-06-24 itself, so FND is the first day the next contract owns the slot."""
    assert has_rolled(contract, fnd) is True


def test_front_month_leaves_slot_one_at_fnd_not_first_of_month():
    """The exact defect. H26's FND is 2026-02-23; delivery starts 2026-03-01.
    The old rule kept CTH6 in CTMAR1 for that whole gap."""
    assert resolve_generic('CTMAR1', '2026-02-20').ice_code == 'CTH6'
    assert resolve_generic('CTMAR1', '2026-02-23').ice_code == 'CTH7'
    # ...and stays rolled through the window the old rule got wrong.
    assert resolve_generic('CTMAR1', '2026-02-27').ice_code == 'CTH7'


def test_expiring_contract_loses_its_generic_slot():
    """Inverse direction: the dying contract must stop claiming slot 1, and the
    next one must take it. Previously CTH6 held CTMAR1 while CTH7 -- carrying
    ~7x the volume -- was labelled CTMAR2."""
    before_h6 = ice_to_generic('CTH6', '2026-02-20')
    before_h7 = ice_to_generic('CTH7', '2026-02-20')
    assert before_h6.generic_code == 'CTMAR1'
    assert before_h7.generic_code == 'CTMAR2'

    after_h7 = ice_to_generic('CTH7', '2026-02-23')
    assert after_h7.generic_code == 'CTMAR1'


def test_july_roll_at_n26_fnd():
    """CTJUL1 -- the blank front month that started this investigation."""
    assert resolve_generic('CTJUL1', '2026-06-23').ice_code == 'CTN6'
    assert resolve_generic('CTJUL1', '2026-06-24').ice_code == 'CTN7'


def test_second_position_follows_the_roll():
    """Position 2 must shift in lockstep, or a roll silently collapses two
    slots onto one contract."""
    assert resolve_generic('CTMAR2', '2026-02-20').ice_code == 'CTH7'
    assert resolve_generic('CTMAR2', '2026-02-23').ice_code == 'CTH8'


# ---------------------------------------------------------------------------
# LOU'S SHAPE RULE -- cross-check only, and the holiday case that proves it
# ---------------------------------------------------------------------------

def _five_business_days_before_first_bd(year, month):
    d = dt.date(year, month, 1)
    while d.weekday() > 4:
        d += dt.timedelta(days=1)
    count = 0
    while count < 5:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


@pytest.mark.parametrize('contract,year,month', [
    ('CTH26', 2026, 3), ('CTK26', 2026, 5), ('CTN26', 2026, 7),
])
def test_shape_rule_agrees_on_the_vendored_dates(contract, year, month):
    """Lou's rule: FND = 5 business days before the 1st business day of the
    delivery month. It agrees on all three vendored contracts, which is the
    independent check that no date was mistyped when hand-entering them."""
    assert first_notice_day(contract) == _five_business_days_before_first_bd(year, month)


def test_shape_rule_is_a_crosscheck_not_a_source():
    """CTZ26: the rule computes 2026-11-24 but ICE says 2026-11-23 (a holiday
    shifts the count). The authority must win -- this asymmetry is the whole
    reason business-day math may never become the source of an FND."""
    assert first_notice_day('CTZ26') == dt.date(2026, 11, 23)
    assert _five_business_days_before_first_bd(2026, 12) == dt.date(2026, 11, 24)


# ---------------------------------------------------------------------------
# AUDIT REGRESSIONS (Sonnet, 2026-09-02) -- both confirmed live before fixing
# ---------------------------------------------------------------------------

def test_cache_is_keyed_per_commodity():
    """An unkeyed cache let the first commodity asked poison every other one.

    _table('CT') populated a single global dict with CT-only rows; the next
    _table('KC') got that same object back, found no KC contracts, and reported
    every KC contract undatable for the life of the process. Ingest swallows
    ExpiryUnavailable into None (normalize.to_generic), so that surfaced as
    generic_code NULL on every KC/CC/SB row -- the same silent defect
    normalize.py's docstring records having already fixed once.
    """
    expiry_source.reset_cache()
    ct = expiry_source._table('CT')
    kc = expiry_source._table('KC')
    assert ct is not kc
    assert any(k.startswith('KC') for k in kc), 'KC table must hold KC contracts'
    # CT must still resolve after KC was asked, and vice versa.
    assert first_notice_day('CTZ26') == dt.date(2026, 11, 23)


def test_fnd_is_not_always_before_the_delivery_month():
    """The premise an earlier fallback relied on is FALSE, and this pins it.

    The live gateway carries 12 SB contracts whose FND lands ON or AFTER the
    1st of their delivery month (e.g. SBV26 fnd 2026-10-01, delivery starts
    2026-10-01). CT/KC/CC show none. Any rule that assumes 'delivery started,
    therefore rolled' would call a live SB contract dead.
    """
    fnd = first_notice_day('SBV26')
    assert fnd >= dt.date(2026, 10, 1)
    assert has_rolled('SBV26', '2026-09-30') is False
    assert has_rolled('SBV26', fnd) is True


@pytest.mark.parametrize('contract,fnd', [
    ('CTZ25', dt.date(2025, 11, 21)),
    ('CTV25', dt.date(2025, 9, 24)),
])
def test_2025_contracts_are_datable(contract, fnd):
    """The archive reaches back to 2025-12-22 and resolving a generic there
    walks candidate years from 2025, so CTZ25 must be datable or every
    Dec-2025 row refuses. Sourced from Bloomberg, not computed."""
    assert first_notice_day(contract) == fnd


def test_whole_archive_range_resolves_without_refusing():
    """End-to-end: every (date, contract) pair the archive actually holds must
    resolve. A refusal here means a real contract has no sourced FND, which
    would silently NULL its generic_code through the ingest layer."""
    for ds, ice in [('2025-12-22', 'CTZ6'), ('2025-12-31', 'CTH6'),
                    ('2026-02-23', 'CTH7'), ('2026-06-24', 'CTN7'),
                    ('2026-07-02', 'CTZ6')]:
        # Must RESOLVE, not merely not-raise: a None here would become a NULL
        # generic_code in the archive via normalize.to_generic's except-None.
        info = ice_to_generic(ice, ds)
        assert info is not None, f'{ice} on {ds} did not resolve to a generic'
        assert info.generic_code.startswith('CT')
