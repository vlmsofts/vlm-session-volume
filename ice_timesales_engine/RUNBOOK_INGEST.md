# RUNBOOK — DAILY INGEST

**Status: BUILT, PARTIALLY PROVEN LIVE.** The batch has been run live by hand
(exit 0, all four commodities, zero rows added). The **15:00 scheduled task has
not yet fired on its own** — the first live 15:00 run is what proves it.

---

## What runs, and when

| Task | Time | Role |
|---|---|---|
| `VLM ICE Timesales Engine - Daily Ingest 1500` | **15:00 ET** | **primary** — puts the session on the dashboard ~2h10m earlier |
| `VLM ICE Timesales Engine - Daily Ingest` | **17:10 ET** | **backstop, unchanged** — keeps its ~2h45m margin and its zero-missed-run record |

Both run the same `run_daily_ingest_all.bat`, which loops
`python -m jobs.daily_ingest --commodity {CT,KC,SB,CC}` and writes to
**Supabase** (`DATABASE_URL`), not local SQLite.

### Why 15:00

Blotters land **14:22-14:25** (median 14:25 over 37 forward-captured sessions;
15 of the last 16 at 14:22-14:23). The futures settle lands **~14:41**. 15:00
clears both with margin.

The blotter write is itself event-driven — `run_cotton_blotter.bat` blocks on
`wait_for_settle.py` and **skips rather than capturing stale settles** — so the
file is either complete or absent, never half-written by the clock.

### Why 17:10 stays

A late or recovered capture. The two known late sessions were 2026-07-01 19:39
and 2026-08-19 17:54. 17:10 catches recoveries that 15:00 is too early for; on
a normal day it is a no-op that adds zero rows.

---

## THE ONE THING TO KNOW: 15:00 is futures-complete, options-partial

At 15:00 the **futures settle exists** but **`settled_surface_*` usually has
not landed**. Measured on the last five CT sessions, the surface is far less
punctual than the blotter:

| session | blotter | settled_surface |
|---|---|---|
| 2026-08-17 | 14:23 | 15:14 |
| 2026-08-18 | — | **14:59** (before 15:00) |
| 2026-08-19 | 14:28 | **17:09** (the late-recovery day) |
| 2026-08-20 | 14:23 | 15:32 |
| 2026-08-21 | 14:23 | 15:32 |

So the surface lands anywhere from 14:59 to 17:09. **Do not assume ~15:32.**
A 15:00 run sometimes sees a surface and usually does not, which is exactly why
this must not be relied on either way.

**Futures volume — everything this ingest computes — is unaffected.** The
pipeline reads `futures_blotter_*.csv`, `spreads_*.csv` and
`futures_settle_*.csv`. It never reads a settled surface.

But if anything downstream is ever wired to read the surface, understand that
**after the 15:00 run the day is futures-complete and options-partial**. The
17:10 run is the first moment both exist. Do not add a surface-dependent step
to this ingest without moving it to the 17:10 task or accepting that gap.

---

## Safe to run twice — by construction, not by luck

1. `upsert_ticks` is `ON CONFLICT DO NOTHING` on
   `(commodity, session_date, ice_code, seq_num)` — re-reading a file inserts
   0 rows rather than duplicating.
2. The sha256 skip only fires on an **identical** file, so a file that **grew**
   between 15:00 and 17:10 is re-read in full.
3. `minute_agg` and `bar5m` are delete-and-reinsert per day.

**Proven on a real day (2026-08-24, all four commodities):**

```
cmd    ticks before  ticks after   delta   lots delta
CT          1155932      1155932      +0           +0
KC          2000476      2000476      +0           +0
SB          3082497      3082497      +0           +0
CC          1900124      1900124      +0           +0
```

A partial 15:00 read followed by a complete 17:10 read is safe.

---

## No ICE / COM contention

15:00 falls inside the 13:35-15:30 window where identical ICE calls measured
~120s against 2-6s quiet, and where the 2026-08-18 missed captures happened.
**This ingest reads FILES only** — verified across all 19 repo modules it
imports: no `win32com`, no `pythoncom`, no `Dispatch`, no `ice.get_*`.

The single network call is a Cloudflare edge purge, which is **unconfigured on
this box** (returns immediately) and is 15s-timeout-capped and
exception-guarded when it is not.

**15:00 costs the shared ICE COM session nothing.**

---

## The log — read this when something looks wrong

`logs/run_daily_ingest_all.log`, appended by every run.

Before 2026-08-24 this file **did not exist** despite the batch's own comment
telling you to check it, and the batch ended `exit /b 0` unconditionally — so a
commodity could fail every day while Task Scheduler reported success.

Now:

- Per-commodity `[ OK ]` / `[FAIL]` plus the full pipeline output.
- **Real exit code**: 1 if any commodity fails, naming which. Visible in Task
  Scheduler's Last Result.
- Trading-calendar gated (`is_trading_day.py`): weekends and ICE holidays log
  `[SKIP]` and exit 0.
- Missing `DATABASE_URL` refuses to run and exits 1, rather than silently
  falling back to local SQLite.

All three paths were tested, including a sabotaged copy that forced every
commodity to fail — exit 1, each one named.

### Reading it

| Line | Meaning |
|---|---|
| `unchanged (sha256 match) -- skipped` | normal on the second run of a day |
| `no blotter files -- zero volume day` | **not** a failure; by design |
| `holiday -- no work` | session date is an ICE holiday |
| `NOTE: ... settle volume is PRIOR-SESSION` | pre-cutover settle file, no `CumVolume`; reconcile deltas carry a one-session skew. See `SAME_DAY_VOLUME_CUTOVER.md` |
| `[FAIL] <CMD>` | that commodity failed; the run exits 1 |

---

## Manual run

```
cd "...\ice_timesales_engine"
run_daily_ingest_all.bat                       # all four, gated, logged
py -3.14 -m jobs.daily_ingest --commodity CT --date 2026-08-21   # one day
py -3.14 is_trading_day.py 2026-08-22          # test the gate
```

---

## Scheduled to retire

**`is_trading_day.py` in this repo is a deliberate thin copy and MUST retire
when the vlm-calendar project lands as the single authoritative calendar.** It
exists only so this repo has no path dependency on the capture repo; the
calendar data itself is already shared through `config.CLOSED_DATES`. Do not
let it quietly become permanent.

---

## What was NOT built, deliberately

**An event watcher on the pulse's completion commit.** It would fire ~14:25,
about 35 minutes earlier than the 15:00 task. Not worth a new resident process:
the watcher history in this program argues against it, and a clock task with a
backstop has no daemon to die silently. Parked, not rejected.
