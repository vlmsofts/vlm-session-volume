# ERRORS.md — VLM Session Volume Project

Approaches that failed, and what worked instead. Check this before proposing a
fix to anything in the same neighbourhood.

---

## E-001 · SQL placeholders: `%s` always, never `?` (2026-09-02)

**What didn't work.** `jobs/backfill_generic_code.py` was written with `?`
placeholders, tested against a copy of the local SQLite, and passed cleanly:
correct row counts, idempotent, all verifications green. It would have crashed
on its first `UPDATE` against production.

**Why.** `store/db.py::_sql` translates `%s` **DOWN** to `?` for SQLite and
hands Postgres the SQL verbatim. psycopg does not recognise `?` at all:

```
psycopg.ProgrammingError: the query has 0 placeholders but 5 parameters were passed
```

SQLite accepts `?` natively, so a SQLite sandbox can never catch this class of
bug. The test passing was itself the misleading signal.

**What worked.** `%s` in every SQL string, then verified the binding against
live Postgres with a deliberately non-matching `UPDATE` inside a rolled-back
transaction.

**Note for next time.** A sandbox on a different DB engine does not test the
dialect. Any new write path gets a live no-op bind check before it is trusted.
Read `store/db.py::_sql` before writing SQL for this repo.

---

## E-002 · Never assume a contract-date ordering holds across commodities (2026-09-02)

**What didn't work.** The unknown-contract fallback in
`contract_resolver.resolve_generic` assumed *"First Notice Day always falls
before the delivery month begins"*, reasoning it was ordering rather than
arithmetic. True for CT, KC and CC. **False for sugar.** The live gateway
carries 12 SB contracts whose FND lands ON or AFTER the 1st of their delivery
month:

```
SBV26  fnd 2026-10-01  delivery starts 2026-10-01
SBK27  fnd 2027-05-03  delivery starts 2027-05-01
```

Sugar also has **FND after LTD** (SBK26 fnd 2026-05-01, ltd 2026-04-30). That
is real, not a transcription error — do not "correct" it.

Any rule saying "delivery has started, therefore the contract has rolled" calls
a live SB contract dead.

**What worked.** Bound the certain cases by the delivery month with a full
month of slack on each side, and REFUSE anything nearer the boundary. Sourcing
the real FND (E-003) is always better than widening a guess.

**Note for next time.** Check a proposed date rule against all four commodities
on the live gateway before relying on it. CT's shape is not the softs' shape.

---

## E-003 · Measure scope against the LIVE store, not the local seed (2026-09-02)

**What didn't work.** The archive backfill was scoped from
`ice_timesales_engine/data/ice_timesales.db` — reported as 4,200 rows in one
table, CT only.

Both numbers were wrong. `DATABASE_URL` is set at OS level to Supabase, so the
engine reads **cloud**; the local SQLite is a stale CT-only seed ending
2026-07-02. The live store holds **11.05M rows across three tables covering CT,
KC, CC and SB**. True scope: **47,329 rows**.

Scoping from the seed also hid four expired non-CT contracts (CCK26, KCK26,
SBK26, SBN26) that blocked 231,122 rows.

**What worked.** `connect(read_only=True)` against the live store for every
measurement, and re-deriving the plan there before writing anything.

**Note for next time.** Check `config.DATABASE_URL` FIRST. If it starts with
`postgres`, the local `.db` file is a seed, not the data.

---

## E-004 · A verification that shares the code under test proves nothing (2026-09-02)

**What didn't work.** The backfill's post-apply check re-ran the same resolver
that produced the changes. It proved "the database agrees with this code" — it
would have passed just as happily if `expiry_source` had returned garbage for a
whole commodity.

**What worked.** An independent check that asks the resolver nothing: where both
slots of a month word are present on a date, slot 1 must hold the
earlier-expiring contract. Sabotage-tested by swapping two labels — it fails
loudly.

**Note for next time.** After any backfill, verify against a property the
producing code never consulted. And sabotage every new guard: a guard that does
not go red when broken is not a guard.

---

## E-005 · Re-derived labels rot silently on a resolver change (2026-09-02)

**What didn't work.** Commit `3639e00` (2026-08-01) fixed the KC/CC/SB
null-generic bug and backfilled the three database tables (~2.98M rows). It did
not touch `data/history/futures_session_volume_history_by_contract_ICE.csv`,
which **re-derives** `generic_code`, `delivery_year` and `position` at write
time via `ingest/rollup.py::emit_contract_rows` rather than copying the column.

Result: 359 blank KC/CC/SB labels sat in that file for a month, quietly
disagreeing with the database.

**What worked.** `jobs/backfill_generic_code.py` now repairs the DB **and** the
sidecar in one pass, and verifies both.

**Note for next time.** After ANY resolver change, re-run that job. The
inventory of which artifacts re-derive versus merely aggregate is in the
user-level memory note `derived-label-artifacts`.
