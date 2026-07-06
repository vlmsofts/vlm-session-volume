"""
bbg_map.py -- Bloomberg intraday-tick conditionCodes -> engine primary_type.

Bloomberg's tick tape (IntradayTickRequest, TRADE events, WITH
includeNonPlottableEvents=True -- without that flag the leg/EFS/EFP/block
prints are silently suppressed and only outrights return) carries its own
condition vocabulary, NOT ICE's. Mapping verified against the ICE tape on
2026-07-01/07-02 across CTZ26/CTH27/CTK27 -- every bucket matched the ICE
blotter classification to the exact lot except a residual 'I' code
(2-34 lots/day, mapped to 'other'):

  Bloomberg code(s)        engine primary_type   verified vs ICE
  '' / NDOO / NDOT / RFC   outright              13,247 == 13,247 (07-02 Z26)
  ST,* (spread-traded)     leg                    4,766 ==  4,766
  EFS                      efs                      207 ==    207
  P (alone)                efp                       47 ==     47
  *X                       efs_delete                99 ==     99
  B,*                      block                     63 ==     63 (07-01)
  I / anything unknown     other                 (2-34 lots/day residual)

Precedence mirrors the engine ladder (classifier.primary_type):
  efs_delete > efp > efs > block > leg > outright.
Unknown codes fall to 'other' and are surfaced by the caller for logging --
never silently absorbed into outright.
"""

# codes that are pure outright annotations (aggressor/off-book markers)
_OUTRIGHT_OK = {'NDOO', 'NDOT', 'RFC'}


def map_bbg_conditions(cc: str) -> str:
    """One Bloomberg conditionCodes string -> engine primary_type."""
    if not cc or not cc.strip():
        return 'outright'
    parts = {p.strip() for p in cc.split(',') if p.strip()}
    if '*X' in parts:
        return 'efs_delete'
    if parts == {'P'}:
        return 'efp'
    if 'EFS' in parts:
        return 'efs'
    if 'B' in parts:
        return 'block'
    if 'ST' in parts:
        return 'leg'
    if parts <= _OUTRIGHT_OK:
        return 'outright'
    return 'other'
