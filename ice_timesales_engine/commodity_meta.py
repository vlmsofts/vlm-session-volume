"""
commodity_meta.py -- per-commodity month-sets + display metadata.

Verified from ice_eod_capture_softs.py (2026-07-04 session):
  CT bona-fide H K N Z   (Oct=V / Aug=Q excluded)
  KC bona-fide H K N U Z (V is serial in KC)
  CC bona-fide H K N U Z
  SB bona-fide H K N V   (NO December; V is bona-fide in SB)

The UI month picker and any month aggregation MUST read from this map --
never assume CT's Mar/May/Jul/Dec shape (esp. SB has no Dec).
"""

COMMODITY_MONTHS = {
    'CT': frozenset('HKNZ'),
    'KC': frozenset('HKNUZ'),
    'CC': frozenset('HKNUZ'),
    'SB': frozenset('HKNV'),
}

MONTH_NAME = {
    'F': 'Jan', 'G': 'Feb', 'H': 'Mar', 'J': 'Apr', 'K': 'May', 'M': 'Jun',
    'N': 'Jul', 'Q': 'Aug', 'U': 'Sep', 'V': 'Oct', 'X': 'Nov', 'Z': 'Dec',
}
MONTH_NUM = {
    'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6,
    'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12,
}

COMMODITY_NAME = {
    'CT': 'Cotton No. 2',
    'KC': 'Coffee C',
    'CC': 'Cocoa',
    'SB': 'Sugar No. 11',
}

SUPPORTED_COMMODITIES = tuple(COMMODITY_MONTHS)


def active_months(commodity: str) -> frozenset:
    return COMMODITY_MONTHS[commodity.upper()]


def month_picker(commodity: str) -> list:
    """[{code, name, num}] sorted by calendar month, for the UI picker."""
    return [
        {'code': m, 'name': MONTH_NAME[m], 'num': MONTH_NUM[m]}
        for m in sorted(active_months(commodity), key=MONTH_NUM.get)
    ]
