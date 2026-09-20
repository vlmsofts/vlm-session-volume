"""
tests/test_refresh_seed_seed_only.py -- hermetic tests for refresh_seed.py's
--seed-only flag.

No live Bloomberg, no subprocess, no seed file touched: subprocess.run is
stubbed so every "command" is recorded rather than executed, and the tests
assert on WHICH commands refresh_seed decided to run.

Why the flag exists: Step 2 runs futures_session_volume.py, which was RETIRED
2026-09-09 when ice_timesales_engine took over -- the RTD sidecar it finalizes
from no longer exists, so it only ever prints a WARNING and reports no data.
The scheduled VLM_CT_FutVol_SeedRefresh task only needs the seed CSV current,
so it should pass --seed-only and run clean. Default behaviour must NOT change.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import refresh_seed


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


@pytest.fixture
def recorded(monkeypatch):
    """Record every subprocess.run call instead of executing it."""
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, 'run', _fake_run)
    return calls


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, 'argv', ['refresh_seed.py'] + argv)
    return refresh_seed.main()


def _is_step1(cmd):
    return any('cotton_futures_volume_history_blpapi.py' in c for c in cmd)


def _is_step2(cmd):
    return any('futures_session_volume.py' in c for c in cmd)


class TestSeedOnly:
    def test_seed_only_runs_step1_and_skips_step2(self, monkeypatch, recorded):
        rc = _run_cli(monkeypatch, ['--days', '25', '--seed-only'])
        assert rc == 0
        assert len(recorded) == 1, 'exactly one command should run'
        assert _is_step1(recorded[0])
        assert not any(_is_step2(c) for c in recorded)

    def test_seed_only_still_passes_merge_so_nothing_is_truncated(
            self, monkeypatch, recorded):
        """--merge is what makes Step 1 an upsert instead of an overwrite of a
        44k-row file. Losing it would silently truncate 20 years of history."""
        _run_cli(monkeypatch, ['--days', '25', '--seed-only'])
        assert '--merge' in recorded[0]

    def test_seed_only_honours_the_days_window(self, monkeypatch, recorded):
        _run_cli(monkeypatch, ['--days', '25', '--seed-only'])
        cmd = recorded[0]
        start = cmd[cmd.index('--start') + 1]
        end = cmd[cmd.index('--end') + 1]
        assert len(start) == 8 and len(end) == 8      # YYYYMMDD
        assert start < end

    def test_seed_only_aborts_and_does_not_claim_success_if_step1_fails(
            self, monkeypatch):
        """A failed Bloomberg pull must return non-zero, not a clean 0 just
        because Step 2 was skipped."""
        def _failing_run(cmd, **kwargs):
            return _FakeCompleted(1)

        monkeypatch.setattr(subprocess, 'run', _failing_run)
        rc = _run_cli(monkeypatch, ['--days', '25', '--seed-only'])
        assert rc != 0


class TestDefaultUnchanged:
    def test_default_still_runs_both_steps(self, monkeypatch, recorded):
        """The whole point of a flag: the old behaviour is untouched."""
        rc = _run_cli(monkeypatch, ['--days', '25'])
        assert rc == 0
        assert len(recorded) == 2
        assert _is_step1(recorded[0])
        assert _is_step2(recorded[1])

    def test_default_with_no_args_still_runs_both_steps(
            self, monkeypatch, recorded):
        _run_cli(monkeypatch, [])
        assert len(recorded) == 2
        assert _is_step2(recorded[1])

    def test_dry_run_executes_nothing_either_way(self, monkeypatch, recorded):
        _run_cli(monkeypatch, ['--dry-run'])
        assert recorded == []
        _run_cli(monkeypatch, ['--dry-run', '--seed-only'])
        assert recorded == []


def test_sabotage_step2_is_detectable_at_all(monkeypatch, recorded):
    """Guard-the-guard: prove _is_step2 actually fires on the default path, so
    'no step 2 ran' in the --seed-only tests is a real observation and not a
    matcher that never matches anything."""
    _run_cli(monkeypatch, ['--days', '25'])
    assert any(_is_step2(c) for c in recorded)
