# DEFECT: the API leaks an idle-in-transaction connection

**FILED 2026-08-24. FIXED 2026-08-24 (autocommit for read-only connections).**
Kept as the record of what was wrong, how it was measured, and why the fix is
the one it is. The parked item (d) below is still parked.

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

**That was symptom relief for one migration, and at the time the leak was left
untouched.** It was fixed later the same day -- see "THE FIX" below.

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


---

## THE FIX, applied 2026-08-24

**Chosen: (b) autocommit, via a `read_only` flag.** Not (a) rollback-on-teardown.

`store/db.py`: `Db(..., read_only=False)` and `connect(..., read_only=False)`.
When true, the Postgres connection is opened `autocommit=True`.
`api/app.py` is the ONE caller that opts in.

### Why autocommit and not a teardown hook

The API never writes -- 12 read call sites in `routes_query.py`, zero
`exec` / `execmany` / `commit` (grep-verified before and after). **There is no
transaction worth keeping open, so the honest fix is to not open one.**

A teardown hook would still open a transaction on the first SELECT and hold it
for the whole request, merely closing it at the end. That is strictly more
state for no benefit, and it depends on every future code path remembering to
route through the hook -- the same class of "everyone must remember" that
produced the defect. Autocommit is a property of the CONNECTION, so it covers
all 12 read paths and every path added later, with nothing to remember.

Writers are untouched: `jobs/daily_ingest.py`, `jobs/backfill.py` and
`jobs/seed_bloomberg.py` all call bare `connect()`, keeping the transactional
default their delete-and-reinsert idempotency depends on.

### Measured after the fix

Through the REAL Flask app (`create_app()` + test client), observed from a
SEPARATE connection:

```
at startup, before any request : state='idle'  in_transaction=False  locks=NONE
GET /health, /catalog, /window, /price, /profile, /contracts, /reconcile
AFTER the page load            : state='idle'  in_transaction=False  locks=NONE
```

* connection NOT idle-in-transaction: **True**
* no open transaction: **True**
* no lock on `minute_agg` / `bar5m`: **True**
* idle-in-transaction sessions on the whole database afterward: **0**

Pinned by `tests/test_readonly_connection.py` (6 tests), sabotage-verified:
removing `read_only=True` from `api/app.py` turns
`test_create_app_builds_a_read_only_db` red.

### Bloat, checked at the same time -- NOT material

```
table            live        dead    dead%   last_autovacuum
bar5m            630,825     19,978   3.1%   2026-08-24 15:41
reconcile_flags    1,961        412  17.4%   2026-07-12 21:10
block_supplement     225         67  22.9%   never
ticks          8,139,936          0   0.0%   2026-08-12 21:12
minute_agg     1,158,776          0   0.0%   2026-08-24 15:43
ingest_log         1,962          0   0.0%   2026-07-15 21:10
```

Autovacuum ran on both large tables TODAY, minutes after pid 329945 was
terminated -- consistent with the leak having held the xmin horizon and vacuum
catching up the moment it was released. `minute_agg` and `ticks` are at **0
dead tuples**; `bar5m` at 3.1% is normal churn. The two small tables show high
percentages on trivial absolute counts (412 and 67 rows).

**No VACUUM was run.** None is needed on this evidence.
