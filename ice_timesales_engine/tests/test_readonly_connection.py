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


class TestTheApiSurvivesADroppedConnection:
    """2026-09-26: the local server ran 5 days, the server side dropped its one
    connection, and /catalog 500'd until restart. routes_query._db() now pings
    once per request and reopens on failure."""

    def test_a_dead_connection_is_replaced_and_the_request_succeeds(self, tmp_path):
        app = create_app(str(tmp_path / 'drop.db'))
        dead = app.config['DB']
        dead.conn.close()                       # simulate the server dropping it
        r = app.test_client().get('/v1/sessionvol/catalog')
        assert r.status_code == 200
        fresh = app.config['DB']
        assert fresh is not dead
        assert fresh.read_only is True, 'the reopened Db must stay read-only'
        fresh.close()

    def test_reconnect_runs_no_ddl(self, tmp_path, monkeypatch):
        """Reopen must use Db(), not connect(): the API never writes."""
        app = create_app(str(tmp_path / 'noddl.db'))
        app.config['DB'].conn.close()
        calls = []
        monkeypatch.setattr(dbmod.Db, 'init_schema', lambda self: calls.append(1))
        app.test_client().get('/v1/sessionvol/catalog')
        assert calls == [], 'reconnect must not run init_schema DDL'
        app.config['DB'].close()

    def test_a_healthy_connection_is_kept_and_pinged_once_per_request(self, tmp_path):
        app = create_app(str(tmp_path / 'ok.db'))
        db = app.config['DB']
        pings = []
        real_q = db.q
        db.q = lambda sql, params=(): (pings.append(1) if sql == 'SELECT 1' else None) or real_q(sql, params)
        client = app.test_client()
        for _ in range(2):                      # catalog calls _db() for every commodity
            assert client.get('/v1/sessionvol/catalog').status_code == 200
        assert app.config['DB'] is db
        assert len(pings) == 2, 'one ping per request -- not per _db() call, not per process'
        db.close()

    def test_a_dead_connection_is_repinged_even_under_an_outer_app_context(self, tmp_path):
        app = create_app(str(tmp_path / 'ctx.db'))
        client = app.test_client()
        with app.app_context():
            assert client.get('/v1/sessionvol/catalog').status_code == 200
            app.config['DB'].conn.close()
            assert client.get('/v1/sessionvol/catalog').status_code == 200
        app.config['DB'].close()

    def test_db_down_gives_500_then_recovers(self, tmp_path, monkeypatch):
        """If reopening fails too: a 500, config['DB'] untouched, and the next
        request retries rather than being stuck."""
        import api.routes_query as rq
        app = create_app(str(tmp_path / 'down.db'))
        dead = app.config['DB']
        dead.conn.close()
        real_db = rq.Db

        def refuse(*a, **k):
            raise ConnectionError('db unreachable')
        monkeypatch.setattr(rq, 'Db', refuse)
        client = app.test_client()
        assert client.get('/v1/sessionvol/catalog').status_code == 500
        assert app.config['DB'] is dead
        monkeypatch.setattr(rq, 'Db', real_db)
        assert client.get('/v1/sessionvol/catalog').status_code == 200
        assert app.config['DB'] is not dead
        app.config['DB'].close()
