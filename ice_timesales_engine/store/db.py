"""
db.py -- connection + dialect shim.

DATABASE_URL starting postgres:// or postgresql://  -> psycopg (Supabase, prod)
anything else / unset                               -> SQLite file (dev/test)

The shim exposes one interface either way:
  conn = connect();  q(conn, sql, params) -> list[tuple];  execmany(...); commit
SQL in this repo is written with %s placeholders (psycopg style); the shim
rewrites %s -> ? for SQLite. Keep to the portable subset (see schema.sql).
"""

import os
import sqlite3
from datetime import datetime, timezone

import config


def _is_postgres(url: str) -> bool:
    return url.startswith('postgres://') or url.startswith('postgresql://')


class Db:
    """Thin dual-dialect wrapper. One instance per process is fine."""

    def __init__(self, database_url: str = None):
        url = database_url if database_url is not None else config.DATABASE_URL
        self.is_postgres = _is_postgres(url)
        if self.is_postgres:
            import psycopg
            self.conn = psycopg.connect(url)
        else:
            path = url or config.DEFAULT_SQLITE_PATH
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # check_same_thread=False: the Flask API (threaded) only READS;
            # all writes happen in the single-process ingest jobs.
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.execute('PRAGMA journal_mode=WAL')
        self.path = None if self.is_postgres else (url or config.DEFAULT_SQLITE_PATH)

    # -- dialect helpers ----------------------------------------------------
    def _sql(self, sql: str) -> str:
        return sql if self.is_postgres else sql.replace('%s', '?')

    def q(self, sql: str, params=()) -> list:
        cur = self.conn.execute(self._sql(sql), params) if not self.is_postgres \
            else self._pg_exec(sql, params)
        return cur.fetchall()

    def exec(self, sql: str, params=()) -> int:
        cur = self.conn.execute(self._sql(sql), params) if not self.is_postgres \
            else self._pg_exec(sql, params)
        return cur.rowcount

    def execmany(self, sql: str, seq_params) -> int:
        if self.is_postgres:
            with self.conn.cursor() as cur:
                cur.executemany(sql, seq_params)
                return cur.rowcount
        cur = self.conn.executemany(self._sql(sql), seq_params)
        return cur.rowcount

    def _pg_exec(self, sql: str, params):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    # -- schema -------------------------------------------------------------
    def init_schema(self):
        ddl = open(os.path.join(os.path.dirname(__file__), 'schema.sql'),
                   encoding='utf-8').read()
        # Split on ';' -- the DDL contains no ';' inside literals.
        for stmt in ddl.split(';'):
            s = stmt.strip()
            if s:
                self.exec(s)
        self.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(database_url: str = None) -> Db:
    db = Db(database_url)
    db.init_schema()
    return db
