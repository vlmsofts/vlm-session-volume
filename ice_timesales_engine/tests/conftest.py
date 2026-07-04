import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FIXTURES = os.path.join(REPO_ROOT, 'tests', 'fixtures')


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh SQLite Db with schema applied, isolated per test."""
    from store.db import connect
    db = connect(str(tmp_path / 'test.db'))
    yield db
    db.close()


@pytest.fixture
def fixture_ice_root(monkeypatch):
    """Point config.ICE_ROOT at tests/fixtures (contains CT/2026-07-02)."""
    import config
    monkeypatch.setattr(config, 'ICE_ROOT', FIXTURES)
    return FIXTURES
