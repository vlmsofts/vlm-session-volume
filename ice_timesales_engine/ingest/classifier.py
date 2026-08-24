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
# CANCELLED buckets mirror the live ladder, one per base type. R11 keys on the
# Delete TAG (see below), and the tag is orthogonal to trade type, so a cancelled
# print keeps its type identity instead of being flattened into one bucket. The
# legacy name 'efs_delete' is DELIBERATELY UNCHANGED -- it is what a cancelled
# EFS has always been called, it is stored in ticks/minute_agg/bar5m on disk, and
# renaming it would break every stored row and every explicit types= query.
PRIMARY_TYPES = ('efs_delete', 'efp_delete', 'block_delete', 'leg_delete',
                 'outright_delete', 'other_delete',
                 'efp', 'efs', 'block', 'leg', 'outright', 'other')
# Aggressor sub-split of outright (informational; NOT a separate primary bucket).
OUTRIGHT_SIDES = ('outright_ask', 'outright_bid', 'outright_unstamped')

# ---------------------------------------------------------------------------
# R11 -- CANCELLED PRINTS NEVER COUNT.  THE SINGLE SOURCE OF THIS RULE.
# ---------------------------------------------------------------------------
# A Delete-tagged print is a BUSTED trade: a trade ICE cancelled. It is not
# flow. It must never land in a default volume tally, window sum, chart, table
# or client-facing number. Established from ICE's own tape (Seq 2525020/1/2 --
# a 06:27:24 busted print with a 06:40:00 clean re-entry).
#
# KEYED ON THE TAG, NOT ON A BUCKET NAME [widened 2026-08-24, Lou's ruling].
# Delete is ORTHOGONAL to trade type: a cancelled block is cancelled. The first
# pass keyed on the single bucket name 'efs_delete', which -- because the ladder
# puts BlockTrde above Leg above the aggressor tags -- silently missed every
# cancelled print that was not an EFS. Measured over the whole CT blotter
# corpus: 922 cancelled lots were excluded and 340 still counted
# ('BlockTrde, Leg, Delete' 186, 'BlockTrde, Delete' 127, 'EFP, Delete' 22,
# 'Leg, Delete' 5). That was an implementation artifact, never the ruling.
#
# WHY A BUCKET PER BASE TYPE, and not a boolean column. minute_agg and bar5m are
# keyed on (.., primary_type) ONLY -- no tag column exists at that grain, so a
# cancelled block aggregated there is INDISTINGUISHABLE from a live block and no
# SQL predicate can recover it (proven on a live rebuild). Carrying the tag in
# the bucket is what makes the exclusion expressible in the aggregate tables and
# keeps cancelled volume retrievable per type. Cancelled EFS keeps its historic
# name 'efs_delete' so stored rows and existing queries stay valid.
#
# The row is KEPT in ticks/minute_agg/bar5m. What this rule governs is who SUMS
# it: cancelled volume is excluded from every default and stays retrievable on
# explicit request -- types=['efs_delete'] still works, and CANCELLED_TYPES /
# is_cancelled() address the whole set across every bucket it now spans.
#
# ONE RULING, ONE CONSTANT. Every consumer imports EXCLUDED_FROM_CLEAN or calls
# clean_split(); nobody writes "primary_type != 'efs_delete'" a second time.
# Two copies of a rule is how this drifted in the first place.
#
# Scope note, deliberate: plain EFS, EFP, BlockTrde and Leg are NOT excluded.
# ICE counts them, verified exact against the official Daily Market Report
# (17/17, 315/315, 100/100). ONLY the Delete tag marks cancelled flow.
CANCELLED_SUFFIX = '_delete'
EXCLUDED_FROM_CLEAN = ('efs_delete', 'efp_delete', 'block_delete',
                       'leg_delete', 'outright_delete', 'other_delete')


def is_excluded(primary: str) -> bool:
    """True if `primary` is cancelled flow and must not count in a default sum."""
    return primary in EXCLUDED_FROM_CLEAN


# is_cancelled is an ALIAS, not a second rule: same tuple, same membership. It
# exists so retrieval code reads as intent ("give me the cancelled flow") rather
# than as exclusion, now that cancelled volume spans several buckets.
is_cancelled = is_excluded
CANCELLED_TYPES = EXCLUDED_FROM_CLEAN


def cancelled_type_for(base: str) -> str:
    """The cancelled counterpart of a live bucket ('block' -> 'block_delete')."""
    return f'{base}{CANCELLED_SUFFIX}' if base != 'efs' else 'efs_delete'


def base_type_of(primary: str) -> str:
    """The live bucket a cancelled one mirrors ('block_delete' -> 'block').
    Returns `primary` unchanged when it is not a cancelled bucket."""
    if primary == 'efs_delete':
        return 'efs'
    if primary.endswith(CANCELLED_SUFFIX):
        return primary[:-len(CANCELLED_SUFFIX)]
    return primary


def clean_split(by_type: dict) -> tuple:
    """Split a {primary_type: total} mapping into (clean_total, excluded_total,
    excluded_by_type).

    The excluded half is RETURNED, never silently dropped: per the P6.6
    row-conservation law every discarded lot stays counted, attributable and
    reportable. clean + excluded == the all-in total, always.
    """
    excluded_by_type = {t: v for t, v in by_type.items()
                        if is_excluded(t) and v}
    excluded_total = sum(excluded_by_type.values())
    clean_total = sum(v for t, v in by_type.items() if not is_excluded(t))
    return clean_total, excluded_total, excluded_by_type


def excluded_sql(column: str = 'primary_type') -> tuple:
    """SQL fragment + params excluding cancelled flow from an aggregate.

    Returns (' AND primary_type NOT IN (%s)', ['efs_delete']) so callers keep
    the placeholder style the rest of the query layer uses. Use this instead of
    hand-writing the predicate.
    """
    ph = ','.join(['%s'] * len(EXCLUDED_FROM_CLEAN))
    return f' AND {column} NOT IN ({ph})', list(EXCLUDED_FROM_CLEAN)


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
      0 Delete -> the cancelled twin of whatever the print otherwise is
      1 efp  2 efs  3 block  4 leg  5 outright
    A blank token set (no aggressor stamp) is an OUTRIGHT fill -> 'outright',
    NOT a separate 'blank' bucket. Only genuinely unknown non-empty conditions
    fall through to 'other' (logged at ingest if ever hit).
    """
    # R11 keys on the TAG: a Delete-tagged print is cancelled whatever its type,
    # so resolve the live bucket first and then map it to its cancelled twin.
    # 'EFS, Delete' -> 'efs_delete', preserving the historic name.
    if 'Delete' in tokens:
        return cancelled_type_for(primary_type(tokens - {'Delete'}))
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
