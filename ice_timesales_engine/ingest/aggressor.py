"""
aggressor.py -- ICE aggressor side for a tape print. VENDORED TOKEN RULE ONLY.

===========================================================================
  SetByBid = BUY.   SetByAsk = SELL.
  ICE ticket 0903465452, pinned after a 25-day scan.
  DO NOT re-derive this from first principles. DO NOT "fix" it from a doc.
===========================================================================

ICE's Conditions field names the side whose order SET the price -- the
aggressor -- NOT the book side the trade printed on. A WebICE colour
convention (green = bought on the offer) reads the OPPOSITE way and has caused
this mapping to be "corrected" backwards before: two ad-hoc scripts inverted it
in a single evening by trusting a stale doc.

This is the most inversion-prone fact in the stack and ITS FAILURE MODE IS
SILENT. Flipping it changes the sign of every signed-flow number while the
distribution still looks entirely plausible. If it is ever revisited the
authority is a written ICE confirmation, not intuition, not a colour, not a
dashboard screenshot.

Corroborated across the analyzer at pipeline/gex_signed_flow.py:15,
pipeline/ice_sections.py:316-318, pipeline/ice_attribute.py:174-187 (which
records the empirical check: green/BUY <-> SetByBid, 85 call / 71 put) and
DOCS/GLYPH_RULING_SHEET.md:19. No site anywhere contradicts it.

WHY THIS FILE IS A VENDORED COPY, not an import
-----------------------------------------------
The rule is defined in Options_flow_analyzer/pipeline/outright_flow.py
(aggressor_side). Importing it across repos would make this engine unrunnable
without the analyzer present, which breaks the Railway deployment where the
analyzer does not exist, and would make an engine session's behaviour depend on
an analyzer file. Same shape as is_trading_day.py: duplicated code, single
rule, with a MECHANICAL guard against drift --
tests/test_aggressor_matches_analyzer.py reads the analyzer's own mapping and
asserts this one matches, SKIPPING loudly when the analyzer is absent.

WHAT WAS AND WAS NOT VENDORED
-----------------------------
Vendored: the token-to-side map, and nothing else. The analyzer's function is
already a pure token map with no ruling, hierarchy or vol-tell behaviour, so
there was no ruling layer to leave behind. THE ENGINE HAS NO RULING LAYER AND
MUST NOT PRETEND TO: it buckets tape prints by their stamp. Any interpretation
of what a side MEANS belongs to a consumer, not here.

Deliberately NOT vendored: the analyzer's 'CROSS' state. RFCCross marks a
BROKER-EXECUTED CROSS -- an execution mechanism, NOT a side. A cross has a
buyer and a seller like any trade; the aggressor is obscured by construction
because the broker held both sides and any counterparty can step into the
middle in the central book. So CROSS is a REASON for unsided, never a fourth
side. It is also an OPTIONS-tape token: measured absent from all 375 CT futures
blotter files on disk, whose complete Conditions vocabulary is 12 strings --
Leg, SetByBid, SetByAsk, blank, EFS, EFP, 'BlockTrde, Leg', and the five
Delete-tagged variants. This engine reads futures.

DECIDED 2026-08-24: NO second unsided bucket and no 'crossed' state. RFCCross
on a futures tape is a rare-instance possibility, not worth building plumbing
for. It lands in the ONE unsided bucket like every other unstamped print, and
unexpected_tokens() flags that it occurred -- which costs nothing and is
already required for any unrecognised token. Do not widen this bucket, and do
not add branching to distinguish REASONS for unsided.

THREE STATES, and unsided is not a side
---------------------------------------
BUY and SELL answer WHO WAS AGGRESSIVE. UNSIDED answers nothing -- it is the
honest absence of an answer, and it is NEVER defaulted, folded, or split into
buy/sell. A blank-condition print is a real outright fill that ICE simply did
not stamp (verified 2026-07-02: 454 blank prints at real prices 76.88-77.85),
and 365,887 of them exist across the CT corpus. Guessing a side for those would
manufacture flow that was never observed.

Only OUTRIGHT prints can carry a real side. leg, efs, efp, block and every
cancelled type are unsided BY CONSTRUCTION -- they have no aggressor stamp.
That is asserted in the tests, not assumed.
"""

from .classifier import outright_side, tokenize

# The three states. 'unsided' is the absence of a side, never a side.
SIDES = ('buy', 'sell', 'unsided')
BUY = 'buy'
SELL = 'sell'
UNSIDED = 'unsided'

# THE MAPPING. One place. See the ICE ticket citation above before touching it.
# Keyed on classifier.outright_side()'s tag names so the TOKEN test lives in
# exactly one place (classifier) and the TAG-TO-SIDE map lives in exactly one
# place (here). Two rules, one home each, no duplicated token matching.
_TAG_TO_SIDE = {
    'outright_bid': BUY,      # SetByBid -> the BID set the price -> BUY
    'outright_ask': SELL,     # SetByAsk -> the ASK set the price -> SELL
    'outright_unstamped': UNSIDED,   # no stamp: never guessed
}


def side_for(primary: str, tokens) -> str:
    """Aggressor side for a print, from its primary_type and Conditions tokens.

    Returns 'buy' | 'sell' | 'unsided'. Anything that is not a live outright is
    'unsided' by construction -- a leg, EFS, EFP, block or cancelled print
    carries no aggressor stamp, so there is no side to report. Cancelled
    outrights ('outright_delete') are unsided too: R11 excludes them from every
    default total, and reporting a side for a busted print would invite it back
    into a signed sum.
    """
    if primary != 'outright':
        return UNSIDED
    return _TAG_TO_SIDE.get(outright_side(tokens), UNSIDED)


def side_for_conditions(primary: str, conditions: str) -> str:
    """side_for() from a raw Conditions string (the analyzer's input shape)."""
    return side_for(primary, tokenize(conditions or ''))


def is_sided(side: str) -> bool:
    """True if `side` is a real side. 'unsided' is not."""
    return side in (BUY, SELL)


# Every Conditions token this engine expects on a FUTURES tape. Measured across
# all 375 CT futures blotter files: the complete vocabulary is these 7 tokens,
# combining into 12 distinct strings. RFCCross is NOT here -- it is an
# options-tape token (see the module docstring); if one ever appears on a
# futures tape it lands in 'unsided' and is FLAGGED by unexpected_tokens(),
# which is the whole of the handling by design.
KNOWN_TOKENS = frozenset({'SetByBid', 'SetByAsk', 'Leg', 'EFS', 'EFP',
                          'BlockTrde', 'Delete'})


def unexpected_tokens(tokens) -> frozenset:
    """Tokens this engine has never seen on a futures tape.

    FLAG, never fall through silently. A new ICE token must surface as a
    finding rather than being absorbed into 'unsided' unnoticed -- silent
    absorption is how an unmapped aggressor tag would become invisible flow.
    Returning empty is the normal case; a caller logs whatever comes back.
    """
    return frozenset(tokens) - KNOWN_TOKENS
