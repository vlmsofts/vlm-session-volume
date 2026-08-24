# DEFECT: the API leaks an idle-in-transaction connection

**FILED 2026-08-24. NOT FIXED. Do not fix this bundled with other work.**

Found while migrating `minute_agg` / `bar5m` to add the `side` column. It
blocked that migration outright, which is how it surfaced.

---

## What was measured

A live production session sat **idle inside an open transaction for 1h50m**,
holding `AccessShareLock` on both `minute_agg` and `bar5m`:

```
pid    = 329945
user   = postgres        app = Supavisor
state  = idle in transaction
in transaction for = 01:50:04
last query = SELECT MAX(session_date), MAX(ingested_at) FROM ingest_log
             WHERE commodity=$1 AND status='ok'

locks held:
  pid=329945  minute_agg  AccessShareLock  granted=true
  pid=329945  bar5m       AccessShareLock  granted=true
```

That query is `store/repository.py::freshness()`.

Server settings at the time:

```
statement_timeout                   = 2min
idle_in_transaction_session_timeout = 0     <-- nothing reaps these, ever
lock_timeout                        = 0
```

## Root cause

`api/routes_query.py::_db()` returns a **single long-lived connection** held in
`current_app.config['DB']`:

```python
def _db():
    return current_app.config['DB']
```

**No code path in `api/` ever calls `commit()`, `rollback()` or `close()` on a
read.** Verified by grep across `api/*.py`: zero matches.

Under psycopg3 a plain `SELECT` opens a transaction and holds it until the
connection is committed or rolled back. So every read leaves the transaction
open, and the connection then sits `idle in transaction` indefinitely holding
`AccessShareLock` on every table it touched. `freshness()` is simply the most
frequent read -- it runs on nearly every endpoint via `_freshness()` -- so it is
the query most often seen holding the lock, not the only culprit.

## Two consequences

1. **Any future DDL on these tables blocks.** `ALTER TABLE` needs
   `AccessExclusiveLock`, which cannot be granted while `AccessShareLock` is
   held. This is not theoretical: it cancelled the `side` migration twice at
   the 2min `statement_timeout` before the blocker was identified. Worse, a
   queued `ALTER TABLE` blocks everything queued behind IT, so a naive retry
   can stall unrelated reads.

2. **Vacuum cannot reclaim dead rows while that snapshot is held.** This is the
   one that **gets worse quietly.** An open transaction pins the xmin horizon,
   so `VACUUM` cannot remove tuples newer than it anywhere in the database. The
   daily ingest is delete-and-reinsert per day across `minute_agg` and `bar5m`
   (778k and 459k rows), so it produces a large volume of dead tuples every
   run. With a long-lived open snapshot those are never reclaimed: the tables
   bloat, index scans slow, and nothing visibly fails until performance
   degrades. There is no error to notice.

## What was done on 2026-08-24

`pg_terminate_backend(329945)` after confirming, immediately before the call,
that it was still `idle in transaction`, still on the freshness read, and the
only holder of a conflicting lock. The transaction was idle with a completed
read, so no work was lost. The migration then succeeded in 5.4s and 8.1s with
`lock_timeout = 5s` set.

**That was symptom relief for one migration. The leak is untouched.**

## Fix options, for a session that is about this and nothing else

- **(a) Roll back after each read.** Cheapest correct fix: have the request
  teardown (`teardown_appcontext`) call `rollback()` on the shared connection,
  or wrap reads in an explicit transaction context that closes. Ends the
  idle-in-transaction state without changing the connection model.
- **(b) Autocommit for the read-only API.** The query layer never writes, so
  putting the connection in autocommit means no transaction is ever left open.
  Check `store/db.py` for write paths that share this connection first.
- **(c) Connection-per-request from a pool** instead of one connection in app
  config. Most conventional, largest change.
- **(d) `idle_in_transaction_session_timeout` on the server** as a backstop so
  a future leak is reaped automatically. Durable, but a server-level setting on
  production with its own blast radius -- Lou's ruling 2026-08-24: worth doing
  eventually, **not bundled with a migration**.

(a) or (b) fixes the cause; (d) is a seatbelt for the next one and does not
substitute for either.
