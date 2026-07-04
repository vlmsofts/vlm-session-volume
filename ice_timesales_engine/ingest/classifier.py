"""
classifier.py -- Conditions tokenizer -> primary_type + tag flags.

Conditions vocabulary (verified across all 20 CT blotter files on disk):
  SetByAsk, SetByBid, Leg, EFS, EFP, 'EFS, Delete', 'BlockTrde, Leg', ''
Conditions can be COMPOUND (comma-joined): tokenize on comma, match by tag
MEMBERSHIP, never exact string.

Two distinct concepts (build plan section 0.2):
  * primary_type -- ONE mutually-exclusive bucket per print, assigned by the
    precedence ladder below. This is what sums to the tape total and what
    minute_agg stores.
  * tag flags   -- independent booleans (is_leg, is_block, ...) that drive
    informational "all leg-involved prints" filter views ONLY. Filtered views
    are never summed together, so no double count.
"""

TAGS = {'SetByAsk', 'SetByBid', 'Leg', 'EFS', 'EFP', 'BlockTrde', 'Delete'}

# Primary buckets, in precedence order (first match wins).
# 'outright' covers SetByAsk, SetByBid AND blank-condition prints -- a blank
# print is a genuine outright fill (real price, real size) that ICE simply
# didn't stamp with an aggressor side (verified 2026-07-02: 454 blank prints,
# real prices 76.88-77.85). ask/bid are tracked as sub-splits, not separate
# primary buckets, so the Outright total is complete.
PRIMARY_TYPES = ('efs_delete', 'efp', 'efs', 'block', 'leg', 'outright', 'other')
# Aggressor sub-split of outright (informational; NOT a separate primary bucket).
OUTRIGHT_SIDES = ('outright_ask', 'outright_bid', 'outright_unstamped')


def tokenize(conditions: str) -> frozenset:
    """Split a Conditions string on commas -> set of non-empty tokens."""
    if not conditions:
        return frozenset()
    return frozenset(t.strip() for t in conditions.split(',') if t.strip())


def tag_flags(tokens: frozenset) -> dict:
    """Independent membership booleans -- filter views only, never summed."""
    return {
        'is_outright': 'SetByAsk' in tokens or 'SetByBid' in tokens,
        'is_leg': 'Leg' in tokens,
        'is_efs': 'EFS' in tokens,
        'is_efp': 'EFP' in tokens,
        'is_block': 'BlockTrde' in tokens,
        'is_delete': 'Delete' in tokens,
    }


def primary_type(tokens: frozenset) -> str:
    """Mutually-exclusive bucket via the precedence ladder (first match wins):
      1 efs_delete  2 efp  3 efs  4 block  5 leg  6 outright
    A blank token set (no aggressor stamp) is an OUTRIGHT fill -> 'outright',
    NOT a separate 'blank' bucket. Only genuinely unknown non-empty conditions
    fall through to 'other' (logged at ingest if ever hit).
    """
    if 'EFS' in tokens and 'Delete' in tokens:
        return 'efs_delete'
    if 'EFP' in tokens:
        return 'efp'
    if 'EFS' in tokens:
        return 'efs'
    if 'BlockTrde' in tokens:
        return 'block'
    if 'Leg' in tokens:
        return 'leg'
    if 'SetByAsk' in tokens or 'SetByBid' in tokens or not tokens:
        return 'outright'
    return 'other'   # unknown non-empty condition; log at ingest if ever hit


def outright_side(tokens: frozenset) -> str:
    """Aggressor sub-split for an outright print (informational only).
    'outright_unstamped' = a real outright fill with no ask/bid stamp (blank)."""
    if 'SetByAsk' in tokens:
        return 'outright_ask'
    if 'SetByBid' in tokens:
        return 'outright_bid'
    return 'outright_unstamped'
