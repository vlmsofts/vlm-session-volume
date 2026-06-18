# Handoff Prompt — Session-Window Volume Comparison (Overnight + Day)
## MODE: INVESTIGATE & PLAN ONLY — DO NOT WRITE CODE YET

You are working in the live `Options_flow_analyzer` repo. Everything here is **production**:
a daily pipeline, an RTD tape writer, GEX, settlement watchers, and scheduled jobs are
running against this code and data. Treat it accordingly.

This task has **three gated phases**. You may only do Phase 1 and Phase 2 now. Phase 3
(writing code) is forbidden until I explicitly approve your Phase 1+2 report.

---

## 1. WHAT WE'RE DOING AND WHY (purpose)

We want a **session-window volume comparison** for CT options, built from the existing RTD
options tape (`data/<date>/ct_options_tape.csv`). Two windows per session:

- **Overnight window:** 21:00 (prior day) → 07:00 ET
- **Day window:** 07:00 → 14:20 ET (electronic session close)

At **end of day**, produce ONE report that lists **both** windows side by side and compares
each window's volume to the trailing **5, 10, 20, 30, and 60** completed sessions
(relative-volume / RVOL = this session's window volume ÷ mean of the lookback window).

**Purpose:** give a fast, quantified read each morning and each EOD on whether overnight and
day-session options activity is unusually heavy or light versus recent norms — surfacing flow
inflections (e.g. a 2x+ overnight spike, or a dead day session) per contract and call/put.

**Graceful deepening:** only ~5 sessions of options-tape history exist today (tape saving
started 2026-06-11). The 10/20/30/60 lookbacks must **activate automatically as history
accumulates** — until enough sessions exist, show them as `n/a (have M of N)` rather than
computing a misleading short average. No manual edits later.

---

## 2. CURRENT STATE YOU MUST ACCOUNT FOR (do not ignore — this already exists)

A first, narrow version of the overnight piece was already added:

- **`overnight_volume.py`** (repo root) — computes overnight 21:00→07:00 ET options volume
  per contract, call/put split, RVOL vs a single trailing average; writes
  `data/<date>/overnight_volume.txt` + `.json` and appends `data/history/overnight_volume_history.csv`.
- **Scheduled task `overnight-options-volume`** — fires weekdays ~07:24 ET, runs that script,
  notifies on completion.

Your plan must explicitly decide whether the new dual-window feature **extends, refactors, or
supersedes** `overnight_volume.py` and whether the existing morning scheduled job stays, changes,
or is replaced by an EOD job. Do not silently leave two overlapping implementations.

---

## 3. PHASE 1 DELIVERABLE — COLLISION CHECK

Inspect the repo and report, file by file, every point where this feature could collide with
existing code, data, naming, or jobs. At minimum, investigate and report on each of these:

- **`run.py`** — the master daily pipeline entry (`python run.py --date <D> --commodity CT`,
  invoked by `Run_Daily_Pipeline.bat`). Does the new EOD report belong inside this run, or as a
  separate entry point? What does run.py already produce at EOD?
- **`pipeline/flow_aggregator.py`** — already implements a trailing-sessions `--lookback`
  framework (`DEFAULT_LOOKBACK = 5`, `avail[-lookback:]`). **This overlaps directly with the
  5/10/20/30/60 requirement.** Determine whether to reuse/extend it or justify a separate path.
- **`options_tape.py`** + `.tape_ct.lock` / `.options_tape_ct.lock` — the live tape writer.
  Confirm the new code is strictly read-only against the tape and tolerates reading a file that
  is still being written (partial last row, lock present).
- **`eod_options_brief/run.py`** — existing EOD brief. Does the dual-window report belong here,
  alongside it, or standalone? Avoid duplicate/competing EOD outputs.
- **`config/settings.py`** — central config (commodity-agnostic, EXPIRY_DATES, rate). Session
  window times (21:00 / 07:00 / 14:20) and lookback tiers should likely live here, not be
  hard-coded. Check existing conventions.
- **Data + naming:** `data/<date>/` output filenames and `data/history/*.csv` — list every
  filename you intend to write and confirm none collide with existing files (incl. my
  `overnight_volume.*` and `overnight_volume_history.csv`).
- **Weekly backup copier** (`data/weekly-backup/week-ending-*`) — does any job copy/rename files
  by pattern that would sweep up or clobber the new outputs?
- **Scheduled jobs / Task Scheduler / .bat files** — `Run_Daily_Pipeline.bat`, `Run_GEX.bat`,
  `Run_Weekly_Review.bat`, settle watchers, and the Cowork `overnight-options-volume` task.
  Identify timing or output collisions.
- **Multi-commodity:** the repo convention is commodity-agnostic with one job per commodity
  (KC/SB/CC to follow). Confirm the design won't collide when CT is parameterized to others.

---

## 4. PHASE 1 DELIVERABLE — BLAST-RADIUS CHECK

Separately, report the blast radius — everything that could be *affected or broken* even if not
a direct naming collision:

- **Schema contracts / tests:** 285 tests validate existing CSV schemas (e.g. `enriched.csv`).
  Confirm the feature adds only sidecar/new files and changes **no** existing schema. State which
  tests you'd run to prove nothing regressed.
- **Downstream consumers:** anything that reads `data/<date>/` or `data/history/` (dashboards,
  GEX, market-intelligence, the EOD brief). Could a new file in those dirs be picked up
  unintentionally by a glob?
- **Performance:** the options tape is 20–35 MB and ~200–300k rows/day. Reading it (and N prior
  days for the 60-session lookback) must stay fast and memory-sane. Quantify the worst case
  (60 files) and the read strategy.
- **OneDrive sync:** the folder is cloud-synced. Note any risk from writing files mid-sync or
  reading a cloud-only (not-yet-downloaded) file in an unattended scheduled run.
- **Lock contention / partial reads:** EOD run timing vs the tape writer and settle watchers.
- **Timezone:** windows are defined in **ET** and read from the tape's own timestamps; the
  scheduler fires in local clock time. Confirm the analysis is timezone-correct regardless of
  fire time, and define when the EOD job can safely run (day window closes 14:20 ET).
- **Idempotency:** re-running for the same date must not double-append history or corrupt outputs.

---

## 5. PHASE 1 DELIVERABLE — PRECAUTIONS / ASSURANCES

List the concrete precautions the implementation will follow. Expected minimums:

- Read-only on the tape; tolerate in-progress writes and present locks.
- Purely additive outputs; **no** changes to existing file schemas.
- Idempotent writes (safe re-run for a date; history de-duped by date+window).
- Graceful degradation for not-yet-available lookbacks (`n/a (have M of N)`).
- New unit tests + a `--dry-run`/`--no-write` mode; manual backtest over the 5 existing sessions.
- A single reconciled design (extend vs replace `overnight_volume.py`; one clear scheduled job
  story), with the decision logged in `MEMORY.md` (What / Why / What was rejected) per repo convention.
- A rollback note (what to delete/revert if we back this out).

---

## 6. FUNCTIONAL SPEC (the feature to plan toward)

- **Windows:** overnight = 21:00(prev)→07:00 ET; day = 07:00→14:20 ET. Volume for a window =
  cumulative `call_vol`/`put_vol` per contract at the last tape snapshot ≤ the window end,
  minus the cumulative at the window start (overnight start ≈ 0 at the 21:00 session reset;
  the day window must subtract the 07:00 reading so it isn't double-counting overnight).
  **Validate this delta logic against the tape's reset behavior — see `options_tape.py` notes on
  overnight cumulative vol.**
- **Per contract**, plus call/put split and P/C, plus session totals.
- **EOD report:** both windows listed together; each compared to trailing 5/10/20/30/60 completed
  sessions; RVOL per tier; flag HIGH (≥2x) / LOW (≤0.5x).
- **Auto-deepening lookbacks** as in §1.
- **Outputs additive**, history append-only and idempotent.
- **Holiday-aware:** lookbacks count actual **trading sessions**, never calendar days. CT closed
  dates for 2026 are recorded in `MEMORY.md` (IFUS 2026 calendar, source PDF in `reference/`).
  Skip closed dates in all averages/RVOL; a scheduled run on a closed day (next gap: Fri Jun 19,
  Juneteenth) must exit cleanly with "no session" — not error or write a zero row. The holiday
  list should live in `config/settings.py`. Watch for *shortened* sessions (early closes) where the
  day window may end before 14:20 ET — confirm via ICE Exchange Notices.

---

## 7. OPEN DESIGN QUESTIONS TO RESOLVE IN YOUR PLAN

1. Integrate into `run.py` (master pipeline) vs standalone script + scheduled job?
2. Reuse `pipeline/flow_aggregator.py` lookback machinery vs a dedicated module — with rationale.
3. Extend, refactor, or supersede `overnight_volume.py`? Keep, change, or replace the existing
   `overnight-options-volume` morning scheduled task; add an EOD job?
4. Day-window end: fixed 14:20 ET vs derived from settlement/tape end — which is authoritative?
5. Where session-window times and lookback tiers are configured (`settings.py`?).

---

## 8. WHAT TO HAND BACK (then STOP)

A single written report containing: (1) the collision findings, (2) the blast-radius findings,
(3) the precautions you will take, and (4) a step-by-step implementation plan — file-by-file
changes, new files, config additions, the test plan, the scheduled-job change, and a rollback
plan. **Do not write or modify any code.** End by asking for explicit approval to proceed to
implementation.
