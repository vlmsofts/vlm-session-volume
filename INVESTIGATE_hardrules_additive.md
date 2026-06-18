# Investigate-only: hard-rules + additive-columns for session_volume.py

**MODE: INVESTIGATE & REPORT ONLY — DO NOT WRITE OR MODIFY CODE.**
Produce a collision + feasibility report, then stop and wait for go-ahead. The `full` window is
already done and verified — this is about the remaining locked rules, done **without corrupting any
data already captured.**

## Context (current state, confirmed by reading the file)
`session_volume.py` currently:
- Lives **inside** `Options_flow_analyzer/` and imports `config.settings`
  (`DATA_BASE`, `HISTORY_DIR`, `CT_HOLIDAYS`, `SESSION_VOL_DAY_SPLIT`, `SESSION_VOL_DAY_END`,
  `SESSION_VOL_LOOKBACKS`).
- `_WINDOWS = ('overnight','day','full')`; `_read_tape` aggregates **every contract in the tape**
  with **no October/August exclusion, no 8-slot universe filter, and no generic symbology**.
- Writes `session_volume_history.csv` with `_HIST_FIELDS = [date, commodity, window, total, call,
  put, pc_ratio, overnight_ts, day_ts, generated_at]` — one row per (date, window), idempotent
  rewrite keyed on (date, commodity).

So three locked requirements are not yet enforced: **Oct/Aug exclusion**, the **8-slot capture
universe** (DEC/MAR/MAY/JUL × generic 1 & 2), and **contract symbology** (generic_code, ice_code,
month_code, month_name, 4-digit delivery_year) + a **per-contract permanent history**.

## Guiding principle (owner): additive only — corruption impossible
Do **not** change the meaning of any column or value already being written. The preferred design is
to **add new columns / new files that only this feature uses**, leaving every existing column and
every already-captured row exactly as-is. Investigate the cleanest additive shape.

## What to investigate and report

### A. Collision check
1. Does anything other than `session_volume.py` read `session_volume_history.csv` or the
   `data/<date>/session_volume.{txt,json}` files today? (Confirm the two scheduled jobs and any
   dashboards/globs. We believe nothing else does — verify.)
2. If new columns are appended to `_HIST_FIELDS`, will the idempotent rewrite + `DictReader`
   tolerate **old rows that lack the new columns** (blank-fill) without breaking the running
   morning/EOD jobs? Confirm the read path is forward/backward compatible.
3. Confirm reusing `pipeline/contract_calendar.py` (for generic↔ICE↔year) is **read-only** and adds
   no import cycle. Note it is **December-only** today — resolving H/K/N generics needs a small
   self-contained helper (new code in this feature, not an edit to `contract_calendar.py`).

### B. Feasibility — the additive approach (recommend a concrete shape)
1. **Exclusion / universe as ADDED columns, not a changed total.** Rather than redefining `total`
   to drop Oct/Aug/serials (which would make new rows inconsistent with already-captured all-contract
   rows), evaluate keeping the existing `total/call/put` as-is and **adding parallel columns** for the
   filtered view, e.g. `total_universe, call_universe, put_universe` (8-slot, Oct/Aug excluded,
   serials folded into parent month via the existing roll map `CTU→CTZ, CTX→CTZ, CTF→CTH`). Then both
   views coexist; nothing already written changes meaning. Assess RVOL: should RVOL going forward use
   the universe columns? (Likely yes — but it must degrade cleanly while only universe-bearing rows
   exist, and old rows simply don't contribute to the universe series.)
2. **Per-contract identity + history as a NEW file.** Evaluate a new companion
   `data/history/session_volume_by_contract.csv` (one row per date × window × generic), carrying the
   symbology key (`generic_code, ice_code, month_code, month_name, delivery_year, total, call, put`).
   This is purely additive (new file, this feature only) and is what enables "December over N years."
   Confirm no naming/glob/backup collision.
3. **Backfill feasibility.** Because `full = overnight + day`, and the universe view is a re-filter of
   the same tape: can the universe columns + per-contract history be backfilled for existing sessions
   by re-running in write mode over the tape dates still on disk (≤10 days), and the session-level
   `full` from summing existing overnight+day rows? Report how far back each can be reconstructed.
4. **Impact sizing.** Quantify what the 8-slot universe actually drops from current tapes. (Recent
   tapes contain CTZ6/CTZ7/CTH7/CTK7/CTN6/CTN7 plus serials CTU6/CTX6/CTF7 — so the universe filter
   would fold/keep H/K/N/Z and drop the serials. Show the before/after totals for 1–2 sample sessions
   so the owner sees the magnitude.)

### C. Consumption path (note, not a task yet)
Once these columns/files exist and are published to the gateway source, the data will be reachable
by the Cowork assistant via **VLM API endpoints they will open** (the Phase 3c `/v1/flow/CT/
session-volume/*` routes). So ensure the additive columns + per-contract file are part of what gets
published. No direct file access is assumed downstream.

## Deliverable
A written report: (A) collision findings, (B) the recommended additive column/file shape with exact
new field names and where RVOL should source from, (C) backfill reach + impact sizing, and (D) any
risk that the additive approach can't fully avoid. **Then stop** — no code until approved. After this
report, the owner will revisit the in-repo-vs-separate-repo question with the collision facts in hand.
