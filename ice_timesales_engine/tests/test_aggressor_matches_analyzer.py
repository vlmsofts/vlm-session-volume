"""
CROSS-REPO DRIFT GUARD -- the vendored side map must match the analyzer's.

ingest/aggressor.py is a deliberate VENDORED COPY of the rule defined in
Options_flow_analyzer/pipeline/outright_flow.py::aggressor_side. A cross-repo
import was rejected: it would make this engine unrunnable without the analyzer
present, which breaks the Railway deployment where the analyzer does not exist.
Duplicated code, single rule -- and this test is the mechanical guard that keeps
the copy honest.

IT SKIPS, IT NEVER FAILS, WHEN THE ANALYZER IS ABSENT. Railway must not go red
for a missing sibling repo. The skip message is deliberately loud so an absent
guard is visible in the run output rather than passing silently.

THE MAPPING COLLAPSE IS DELIBERATE, NOT A LOST CASE
---------------------------------------------------
The analyzer returns FOUR states; the engine has THREE. Two analyzer states
collapse to 'unsided', for DIFFERENT REASONS, and both are intentional:

  BUY   -> buy       SetByBid. The bid set the price.
  SELL  -> sell      SetByAsk. The ask set the price.
  UNK   -> unsided   No aggressor stamp at all. The honest absence of an
                     answer -- ICE simply did not stamp the print. Never
                     defaulted to a side.
  CROSS -> unsided   RFCCross: a BROKER-EXECUTED CROSS. This is an EXECUTION
                     MECHANISM, not a side. The trade has a buyer and a seller
                     like any other; the aggressor is obscured by construction
                     because the broker held both sides and any counterparty
                     can step into the middle in the central book. So CROSS is
                     a REASON for unsided, never a fourth side. It is also an
                     options-tape token, measured absent from all 375 CT
                     futures blotter files, and this engine reads futures.

Both land in ONE unsided bucket. There is deliberately no second bucket and no
'crossed' state -- see the ruling in ingest/aggressor.py. An unexpected token
is FLAGGED by aggressor.unexpected_tokens() rather than silently absorbed.
"""

import importlib.util
import os
import sys

import pytest

from ingest.aggressor import (BUY, SELL, UNSIDED, side_for_conditions,
                              unexpected_tokens)
from ingest.classifier import tokenize

ANALYZER = os.path.join(
    os.path.expanduser('~'), 'OneDrive - VLM Commodities LTD', 'Desktop',
    'Options_flow_analyzer', 'pipeline', 'outright_flow.py')

# analyzer state -> engine state. See the docstring for why two collapse.
EXPECTED_COLLAPSE = {
    'BUY': BUY,
    'SELL': SELL,
    'UNK': UNSIDED,      # no stamp
    'CROSS': UNSIDED,    # broker cross: aggressor obscured, not a side
}


def _load_analyzer_fn():
    """Import ONLY aggressor_side from the analyzer, or None if unavailable.

    Loaded by file path rather than as a package so importing it cannot drag in
    the analyzer's own dependencies (it imports pipeline.ice_attribute at module
    scope). If anything at all goes wrong we return None and SKIP -- this guard
    must never be the reason a suite goes red.
    """
    if not os.path.isfile(ANALYZER):
        return None, f'analyzer not present at {ANALYZER}'
    try:
        src = open(ANALYZER, encoding='utf-8').read()
    except OSError as exc:
        return None, f'analyzer unreadable: {exc!r}'
    # Extract just the function's source: importing the module would execute
    # its `from pipeline.ice_attribute import _vlm_key` and fail outside that
    # repo. We compile the function alone, which has no imports of its own.
    start = src.find('def aggressor_side(')
    if start == -1:
        return None, 'aggressor_side not found in the analyzer source'
    end = src.find('\ndef ', start + 1)
    fn_src = src[start:end if end != -1 else len(src)]
    ns = {}
    try:
        exec(compile(fn_src, ANALYZER, 'exec'), ns)          # noqa: S102
    except Exception as exc:                                  # noqa: BLE001
        return None, f'could not compile aggressor_side: {exc!r}'
    fn = ns.get('aggressor_side')
    if fn is None:
        return None, 'aggressor_side did not define'
    return fn, ''


_ANALYZER_FN, _WHY = _load_analyzer_fn()

# Loud skip. An absent guard must be visible in the run output.
_skip = pytest.mark.skipif(
    _ANALYZER_FN is None,
    reason=('CROSS-REPO GUARD SKIPPED -- the vendored aggressor mapping in '
            'ingest/aggressor.py was NOT verified against '
            'Options_flow_analyzer this run. This is expected on Railway and '
            f'any box without the analyzer. Reason: {_WHY}'))


@_skip
class TestVendoredCopyMatchesTheAnalyzer:

    # Every Conditions string measured on the real CT futures tape (all 375
    # blotter files), plus RFCCross, which is options-only and is here purely
    # to pin the CROSS -> unsided collapse.
    @pytest.mark.parametrize('conditions', [
        'SetByBid', 'SetByAsk', '', 'Leg', 'EFS', 'EFP', 'BlockTrde, Leg',
        'EFS, Delete', 'BlockTrde, Leg, Delete', 'BlockTrde, Delete',
        'EFP, Delete', 'Leg, Delete', 'RFCCross', 'SetByBid, Leg',
    ])
    def test_engine_side_matches_the_analyzer_state(self, conditions):
        """For an OUTRIGHT print the two must agree, after the documented
        collapse. Non-outright types are the engine's own construction rule and
        are covered in test_aggressor_side.py, not here."""
        analyzer_state = _ANALYZER_FN(conditions)
        expected = EXPECTED_COLLAPSE[analyzer_state]
        # side_for_conditions applies the engine's construction rule (only a
        # live outright can carry a side), so compare on the outright path --
        # that is where the two mappings are actually meant to agree.
        engine = side_for_conditions('outright', conditions)
        if analyzer_state in ('BUY', 'SELL'):
            assert engine == expected, (
                f'{conditions!r}: analyzer says {analyzer_state}, engine says '
                f'{engine!r}. THE VENDORED COPY HAS DRIFTED. SetByBid=BUY and '
                'SetByAsk=SELL are pinned by ICE ticket 0903465452 -- do not '
                '"fix" this by inverting the engine.')
        else:
            assert engine == UNSIDED, (
                f'{conditions!r}: analyzer says {analyzer_state}, which must '
                f'collapse to unsided, but engine says {engine!r}')

    def test_the_inversion_prone_pair_is_pinned_in_both_repos(self):
        """The single most inversion-prone fact in the stack. If this ever
        fails, the authority is a written ICE confirmation, not intuition."""
        assert _ANALYZER_FN('SetByBid') == 'BUY'
        assert _ANALYZER_FN('SetByAsk') == 'SELL'
        assert side_for_conditions('outright', 'SetByBid') == BUY
        assert side_for_conditions('outright', 'SetByAsk') == SELL

    def test_neither_repo_guesses_a_side_for_an_unstamped_print(self):
        assert _ANALYZER_FN('') == 'UNK'
        assert side_for_conditions('outright', '') == UNSIDED

    def test_cross_collapses_to_unsided_and_is_flagged_not_absorbed(self):
        """CROSS is a reason for unsided, not a side -- and because RFCCross is
        not part of this engine's measured futures vocabulary, it must also
        surface as an unexpected token rather than vanishing quietly."""
        assert _ANALYZER_FN('RFCCross') == 'CROSS'
        assert side_for_conditions('outright', 'RFCCross') == UNSIDED
        assert unexpected_tokens(tokenize('RFCCross')) == frozenset({'RFCCross'})


def test_the_guard_reports_its_own_absence():
    """Meta-test, ALWAYS runs. If the analyzer is missing this states it in the
    output, so 'no failures' is never mistaken for 'the copy was verified'."""
    if _ANALYZER_FN is None:
        print(f'\nCROSS-REPO GUARD INACTIVE: {_WHY}')
    assert True
