"""
SETTLE VOLUME VINTAGE -- futures_settle Volume is NOT that date's volume.

futures_settle_<date>.csv is MIXED VINTAGE. Its Settle column is current-day
(D.Settle == (D+1).PrevSettle, 175/175) but its Volume column is the PRIOR
session's figure, because the capture asked ICE for "Volume", which returns a
flat 0.0 on ICE futures symbols. Measured over 36 CT sessions: 165 contract-days
match the D-1 blotter sum exactly, 5 match same-day.

The 2026-08-24 cutover ADDS a CumVolume column (ICE "Cumulative Volume", the
real same-day total) beside the legacy Volume, which keeps its old meaning so
the 369 historical files and both live readers stay coherent.

These tests pin the reader's contract: prefer CumVolume, fall back to Volume,
and ALWAYS say which vintage was used. Synthetic files only -- no production
path, no ICE call.
"""

import tempfile
from pathlib import Path

import pytest

from ingest.reconcile import _read_settle_volumes

HDR_OLD = 'Date,Contract,Settle,Volume,OpenInt\n'
HDR_NEW = 'Date,Contract,Settle,Volume,CumVolume,OpenInt\n'


def _write(tmp_path, text):
    p = tmp_path / 'futures_settle_2026-08-21.csv'
    p.write_text(text, encoding='utf-8')
    return p


class TestVintageIsAlwaysReported:
    """A caller must never have to GUESS which vintage it is holding."""

    def test_pre_cutover_file_is_flagged_prior_session(self, tmp_path):
        p = _write(tmp_path, HDR_OLD + '2026-08-21,CT Z26,68.5,25252.0,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols == {'CTZ6': 25252.0}
        assert vintage == 'prior_session'

    def test_post_cutover_file_is_same_day(self, tmp_path):
        p = _write(tmp_path,
                   HDR_NEW + '2026-08-21,CT Z26,68.5,25252.0,25315.0,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols == {'CTZ6': 25315.0}, 'CumVolume must win over Volume'
        assert vintage == 'same_day'

    def test_partial_feed_is_flagged_mixed_not_silently_same_day(self, tmp_path):
        """A feed that drops CVol on some rows must NOT read as clean same-day."""
        p = _write(tmp_path, HDR_NEW
                   + '2026-08-21,CT Z26,68.5,25252.0,25315.0,100\n'
                   + '2026-08-21,CT H27,70.0,6500.0,,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols == {'CTZ6': 25315.0, 'CTH7': 6500.0}
        assert vintage == 'mixed'

    def test_no_volume_at_all_is_none_not_a_false_pass(self, tmp_path):
        p = _write(tmp_path, HDR_NEW + '2026-08-21,CT Z26,68.5,,,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols == {}
        assert vintage == 'none'


class TestTheTwoColumnsAreNotInterchangeable:
    """The whole point of the cutover: they carry DIFFERENT sessions."""

    def test_cumvolume_is_preferred_and_differs_from_volume(self, tmp_path):
        # Real 2026-08-21 CTZ6 shape: legacy Volume carries 08-20's total.
        p = _write(tmp_path,
                   HDR_NEW + '2026-08-21,CT Z26,68.5,36498.0,25315.0,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols['CTZ6'] == 25315.0
        assert vols['CTZ6'] != 36498.0, 'must not fall back when CumVolume exists'
        assert vintage == 'same_day'

    def test_legacy_files_still_readable_after_the_cutover(self, tmp_path):
        """369 files on disk have no CumVolume. They must not start failing."""
        p = _write(tmp_path, HDR_OLD
                   + '2026-08-21,CT Z26,68.5,25252.0,100\n'
                   + '2026-08-21,CT H27,70.0,6500.0,100\n')
        vols, vintage = _read_settle_volumes(p, 'CT')
        assert vols == {'CTZ6': 25252.0, 'CTH7': 6500.0}
        assert vintage == 'prior_session'
