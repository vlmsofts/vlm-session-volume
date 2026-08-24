"""
THE API'S CONNECTION MUST NOT SIT IDLE IN TRANSACTION.

api/app.py holds ONE Db for the life of the process and never writes: 12 read
call sites in routes_query, zero exec/execmany/commit. Without autocommit,
psycopg opens a transaction on the first SELECT and holds it until a commit or
rollback that no read path ever makes -- so the connection sits `idle in
transaction` forever, holding AccessShareLock on every table it touched.

Measured consequences, both real:
  * it blocked the 2026-08-24 `side` migration TWICE at the 2min
    statement_timeout (pid 329945, open 1h50m);
  * an open snapshot pins the xmin horizon, so VACUUM cannot reclaim the dead
    rows the daily delete-and-reinsert produces. That one degrades silently.

See DEFECT_IDLE_IN_TRANSACTION.md. These tests pin the contract so a future
change cannot quietly reintroduce it.

No production connection is opened here -- SQLite only.
"""

import store.db as dbmod
from api.app import create_app


class TestTheFlagExists:

    def test_connect_accepts_read_only(self, tmp_path):
        """The contract: connect(read_only=True) is how a reader opts in."""
        db = dbmod.connect(str(tmp_path / 'r.db'), read_only=True)
        assert db.read_only is True
        db.close()

    def test_writers_get_a_transactional_connection_by_default(self, tmp_path):
        """The ingest jobs call bare connect() and MUST stay transactional."""
        db = dbmod.connect(str(tmp_path / 'w.db'))
        assert db.read_only is False
        db.close()


class TestThePostgresConnectionIsAutocommit:
    """Assert on the psycopg call itself, so the test is meaningful without a
    live Postgres. A fake psycopg records what it was handed."""

    def _fake_psycopg(self, calls):
        class _Conn:
            def __init__(self):
                self.autocommit = None

            def cursor(self):
                raise AssertionError('no query should run in this test')

            def close(self):
                pass

        class _Mod:
            @staticmethod
            def connect(url, autocommit=False):
                calls.append({'url': url, 'autocommit': autocommit})
                c = _Conn()
                c.autocommit = autocommit
                return c
        return _Mod

    def _build(self, monkeypatch, read_only):
        calls = []
        fake = self._fake_psycopg(calls)
        import sys
        monkeypatch.setitem(sys.modules, 'psycopg', fake)
        # init_schema would run DDL; the point here is the connect() call.
        monkeypatch.setattr(dbmod.Db, 'init_schema', lambda self: None)
        dbmod.connect('postgresql://user:pw@host/db', read_only=read_only)
        return calls

    def test_read_only_asks_psycopg_for_autocommit(self, monkeypatch):
        calls = self._build(monkeypatch, read_only=True)
        assert calls and calls[0]['autocommit'] is True, (
            'a read-only Db must open psycopg with autocommit=True, or every '
            'SELECT leaves a transaction open holding AccessShareLock')

    def test_a_writer_does_not_get_autocommit(self, monkeypatch):
        calls = self._build(monkeypatch, read_only=False)
        assert calls and calls[0]['autocommit'] is False, (
            'the ingest jobs write in transactions -- autocommit would break '
            'the delete-and-reinsert day-unit idempotency')


class TestTheAppOptsIn:

    def test_create_app_builds_a_read_only_db(self, tmp_path):
        """The actual defect: app.config['DB'] must be the read-only kind.
        This is the assertion that goes red if someone drops the flag."""
        app = create_app(str(tmp_path / 'app.db'))
        db = app.config['DB']
        assert db.read_only is True, (
            "api/app.py must call connect(..., read_only=True) -- see "
            'DEFECT_IDLE_IN_TRANSACTION.md')
        db.close()

    def test_the_api_still_answers_after_the_change(self, tmp_path):
        """Endpoint behaviour is unchanged -- /health still 200."""
        app = create_app(str(tmp_path / 'app2.db'))
        r = app.test_client().get('/health')
        assert r.status_code == 200
        assert r.get_json() == {'ok': True}
        app.config['DB'].close()
