# Phase 3 Authorization — Session-Window Volume Comparison

Your Phase 1+2 report is approved. Its factual claims were independently verified against the
repo (no consumer globs `data/<date>/` for anything but `gex_output.json`; the 10-day tape
cleanup is real; `TAPE_SESSION_END=(15,30)` / `TAPE_BACKUP_TIME=(15,5)` confirmed; tape is
append-mode with msvcrt lock on a separate 1-byte file, so a read-only reader is safe).

You may proceed to implementation — but the plan changes in six locked ways below. The governing
rule from the owner, verbatim in spirit: **if there is ANY interference with existing flow, do not
proceed unless a separate method with ZERO collision risk is applied.** Every decision below serves
that rule.

> ## HARD RULE — OCTOBER IS NEVER CONSIDERED IN ANY CALCULATION
> The October contract (`CTV` / month code `V` / Bloomberg `CTOCT*`) is excluded from **everything**:
> overnight and day volume aggregation, per-contract breakdowns, session totals, every lookback and
> RVOL average, and all generic/symbology mapping. October must never appear in any output, total, or
> comparison target. This is non-negotiable and applies before any other step (filter it out at read
> time). It aligns with the existing repo convention `CT_EXCLUDED_MONTH_PREFIXES = frozenset(['CTV','CTQ'])`
> in `config/settings.py` and Bloomberg's own CT generic chain, which skips Oct (sequences Mar/May/Jul/Dec).
> The active CT month set is therefore **{H=Mar, K=May, N=Jul, Z=Dec}**. August (`CTQ`) is ALSO
> excluded (owner-confirmed) — adopt the repo set `{'CTV','CTQ'}` as-is.

> ## CAPTURE UNIVERSE (LOCKED) — owner-defined
> The only contracts captured, aggregated, and compared are the **four futures months Dec, Mar, May,
> Jul**, each at the **1st and 2nd generic position only** — exactly **8 slots**:
> `DEC1, DEC2, MAR1, MAR2, MAY1, MAY2, JUL1, JUL2` (Bloomberg `CTDEC1` … `CTJUL2`). Each is stored
> together with the actual ICE contract it currently represents (e.g. `DEC1`→`CTZ6`, `DEC2`→`CTZ7`)
> "for clarity of capture." Generic positions 3+ are out of scope. Anything outside this 8-slot
> universe — October, August, all serial/other months — is excluded from every total, lookback, and
> output. For the OPTIONS tape, fold each option into its parent futures-month complex using the
> repo's existing serial→futures roll map (`CTU→CTZ`, `CTX→CTZ`, `CTF→CTH`, etc., in `settings.py`);
> any contract that does not map into the 8-slot universe is dropped.

---

## LOCKED DECISIONS (override the corresponding items in your plan)

### 1. Brand-new, physically separate repo — do NOT add files to `Options_flow_analyzer/`
The owner's words: "this should be a new repo file… there is enough bunched into the analyzer
folder." Create a **new sibling repository** (suggested: `vlm_session_volume/`, a sibling of
`Options_flow_analyzer/`). Nothing for this feature is written into `Options_flow_analyzer/` —
not the script, not config, not tests, not outputs, not history. The only relationship to the
old repo is **read-only** consumption of its tape files.

Suggested layout:
```
vlm_session_volume/
  session_volume.py        # core logic (windows, RVOL tiers, report)
  config.py                # SELF-CONTAINED config (see #2). Imports nothing from the old repo.
  api.py                   # VLM API endpoints (Phase 3c, see #6)
  tests/test_session_volume.py
  data/
    history/session_volume_history.csv   # PERMANENT, append-only forever (see #5)
    <date>/session_volume.{txt,json}
  reference/IFUS_Trading_Hours_Holiday_Calendar_2026.pdf   # copy
  MEMORY.md                # this repo's own decision log
  README.md
```

### 2. Self-contained config — touch ZERO existing files
Do **not** modify `Options_flow_analyzer/config/settings.py` or any existing file. Put all
constants in the new repo's `config.py`: overnight/day window times, the (5,10,20,30,60) lookback
tiers, the CT closed-dates set (from the IFUS 2026 calendar), and the **read-only path** to the
source tapes (`OPTIONS_FLOW_DATA = r"C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\Options_flow_analyzer\data"`).
Derive everything else locally. The whole feature must modify **zero** existing files — confirm
this in your final summary.

### 3. Both windows + both schedules
- Windows: overnight `21:00(prev)→07:00 ET`; day `07:00→14:20 ET` (day vol = cum@14:20 − cum@07:00).
- CLI: `--window {overnight|day|both}` (default `both`), `--commodity CT`, `--date`, `--no-write`.
- Two scheduled jobs (in the NEW repo; replace/disable my old `overnight-options-volume` task):
  - **Morning** ~07:15 ET weekdays → `--window overnight` (the early read / notification).
  - **EOD** ~14:35 ET weekdays → `--window both` (lists both windows vs 5/10/20/30/60).

### 4. Loud failure with the exact file path
The owner: "loud fail on any issues… the fail should include the file path that failed so I know
exactly where to look." Therefore:
- Any genuine error (source tape missing/unreadable, history file unwritable, bad config, a
  date folder that should exist but doesn't) → **raise/exit non-zero** with a message that
  includes the **absolute path** of the offending file and what was being attempted. No silent
  `except: pass` that swallows structural failures.
- The ONE tolerated soft-skip is a single malformed/partial last row of a live tape — skip that
  row, but **log a WARNING that names the file path and line number**. Never hide it.
- Holiday (`date in CLOSED_DATES['CT']`) is NOT a failure: print "CT closed on <date> — no session",
  exit 0, write nothing.
- Scheduled-run failures must surface loudly (non-zero exit + path in the message) so a failed
  EOD run is never silent — see #5 for why that matters.

### 5. PERMANENT history — never delete, indefinite record
The owner: "the history of both sessions SHOULD NOT delete; we should have an indefinite running
record stored for comparisons on a longer timeline going forward." So:
- `data/history/session_volume_history.csv` lives in the NEW repo and is **append-only forever**.
  No cleanup job, no retention window, ever touches it. One row per (date, commodity), carrying
  BOTH windows' totals + call/put split (+ per-contract if you keep a companion file).
- This is also the **only** long-term source for deep lookbacks: the source tapes self-delete at
  10 days, so 20/30/60-session averages must read from this history file, not raw tapes. Because
  of that, the EOD job's reliability is critical (hence the loud-fail requirement in #4). Add a
  `--backfill`/repair path that can reconstruct history rows from any tapes still present
  (≤10 days), so a missed run within the window can be recovered.
- Idempotent: re-running a date overwrites that date's row, never duplicates (fixes the
  unconditional-append bug noted in `overnight_volume.py`).

### 6. Expose via the VLM Data Gateway at the end
The owner: "all of this should be available to VLM API and endpoints established." The target is the
existing VLM Data Gateway (`https://vlmapi.vlmdata.com`, header `X-VLM-API-Key`, read-only GET, v1,
standard `cached`/`stale`/`stale_age_seconds` envelope — see `VLM_API_REFERENCE.md`). Match that
gateway's conventions exactly; do NOT invent a new API style.
- **Pattern:** the gateway serves data by reading source files from GitHub repos via
  `github_reader.read_file()` with a 300s cache and stale-cache fallback (e.g. `oi-dashboard`).
  So the new repo's permanent history (CSV/JSON) should be published to a GitHub source the gateway
  can read, then routes added that wrap it in the standard envelope.
- **Namespace:** fit the already-planned flow namespace. The reference doc lists
  `/v1/flow/CT/parsed` and `/v1/gex/CT/*` as **PLANNED — design spec, not yet implemented**. Add:
  `/v1/flow/{commodity}/session-volume/latest` (both windows + RVOL tiers for the latest session),
  `/v1/flow/{commodity}/session-volume/{date}`, and
  `/v1/flow/{commodity}/session-volume/history` (filterable `from`/`to`/`window` for long-timeline
  comparison). Read the "GEX & Options Flow (PLANNED)" section of `VLM_API_REFERENCE.md` for the
  exact envelope/schema/auth conventions before speccing routes.
- **Sequencing:** plan the routes now; build them only after the core script + jobs are verified.

### 7. Contract symbology — store a query-ready, decade-safe identity
The owner's requirement: long-timeline comparison ("how does December compare over the last 3
years"). This is impossible if a contract is stored only as `CTZ6`, because the single-digit ICE
year code recycles every decade (the `6` in `CTZ6` is 2026, 2036, 2006…). For an INDEFINITE record
this is a real defect — **the 4-digit delivery year must be stored.**

**Generic ticker semantics (owner-confirmed authoritative definition):**
- **Sequence generics** `CT1, CT2…` = relative curve position (front month, 2nd month), roll by
  position as the front expires.
- **Calendar-locked generics** `CTDEC1, CTDEC2…` = a fixed calendar month, N years forward from
  today. As of Jun 2026: `CTDEC1`=CTZ6 (Dec 2026), `CTDEC2`=CTZ7 (Dec 2027), `CTDEC3`=CTZ8,
  `CTDEC4`=CTZ9. When Dec 2026 expires, `CTDEC1` rolls to track CTZ7, and so on.
- Generics **never die** — they represent the *slot*, not the contract, stitching a continuous
  history (what "front December" traded at in 2012/2018/2024). **This is exactly why the generic is
  the right key for an indefinite cross-year record.**
- The same calendar-locked family applies to the other active months — `CTMAR#`, `CTMAY#`, `CTJUL#`
  — but **never `CTOCT#`** (October hard-excluded; see top). Per the locked capture universe, only
  the **1st and 2nd** generic of each month is in scope (DEC1/2, MAR1/2, MAY1/2, JUL1/2).

Critically, the resolution logic ALREADY EXISTS — reuse it, do not reinvent:
- `pipeline/contract_calendar.py` (old repo) provides pure functions:
  `actual_contract('CT',12,2026) -> 'CTZ6'`, `parse_generic('CTDEC2') -> ('CT','DEC',12,2)`,
  `generic_to_actual('CTDEC1', front_year=2026) -> 'CTZ6'`, `actual_to_generic('CTZ6', 2026) ->
  'CTDEC1'`. It already handles the roll (a generic points to a different actual contract across the
  Dec roll) and recovers the full year from the single digit. Import it read-only, or vendor a copy
  of those small pure functions into the new repo. Either way, use the SAME symbology as the rest of
  the system so nothing diverges.
- The VLM gateway already exposes the multi-year series this joins to: `oi_data.csv` carries 40
  month-specific Bloomberg generics (`CTMAR1/2, CTMAY1/2, CTJUL1/2, CTOCT1/2, CTDEC1/2`, column
  `bbg_ticker` e.g. `CTDEC1 Comdty`) with history back to **2008-01-02**. So "December over the last
  N years" is already answerable on OI/settle via the generic Dec series — the new feature just needs
  to carry a matching key so its volume data can be aligned to it.

**Requirement:** every per-contract row in the permanent history stores, at minimum:
`ice_code` (CTZ6), `prefix` (CT), `month_code` (Z), `month_name` (Dec), `delivery_year` (**4-digit**,
2026), and the Bloomberg `generic_code` (CTDEC1) resolved via `contract_calendar.py`. Then a
same-delivery-month-across-years query is a filter on `month_code` grouped by `delivery_year`, and a
join to the VLM OI/settle history is a join on `generic_code`.

**RESOLVED (owner-confirmed) — implement exactly as stated:**
1. **All four months from day one.** Extend the generic resolver (in the NEW repo, not by editing
   `contract_calendar.py`) to resolve H/K/N/Z generics at positions 1 and 2. Same-month-across-years
   must work for Mar/May/Jul/Dec immediately.
2. **Canonical key = generic + ICE + 4-digit year.** Store `ice_code` (CTZ6) + `generic_code`
   (CTDEC1) + 4-digit `delivery_year` + `month_code` + `month_name` on every row. Do NOT store the
   specific 2-digit Bloomberg ticker (derivable if ever needed).
3. **August excluded too.** Adopt the repo's `CT_EXCLUDED_MONTH_PREFIXES = {'CTV','CTQ'}` as-is —
   both October and August out. (Reinforced by the locked 8-slot capture universe above.)

---

## STILL REQUIRED (unchanged from your plan)
- Read-only on tapes; tolerate in-progress writes (per #4 soft-skip rule).
- Graceful lookback degradation: `n/a (have M of N)` until enough PERMANENT history exists.
- New `tests/test_session_volume.py` (hermetic fixtures): window extraction, day-window
  subtraction, per-contract/call-put split, multi-tier RVOL degradation, holiday guard,
  idempotent + permanent history, loud-fail-with-path behavior, `--no-write`.
- Backtest `--no-write` over the 5 existing tape sessions; confirm overnight totals match
  `overnight_volume_history.csv` for equivalence before enabling jobs.
- Own `MEMORY.md` in the new repo: What / Why / What was rejected, incl. the new-repo + permanent-
  history + zero-touch-settings decisions.
- Rollback = delete the new repo + remove its two scheduled jobs + re-enable the old morning task.
  Because zero existing files are modified, rollback cannot affect the existing pipeline.

## DELIVERABLE ORDER
3a) Scaffold new repo + `config.py` + `session_volume.py` + tests; backtest over 5 sessions (no jobs yet).
3b) After verification: register the two scheduled jobs; run in parallel with the old task 2–3 sessions.
3c) Add `api.py` VLM endpoints. Then retire the old `overnight_volume.py` task.

Confirm at the end: (a) zero existing files modified, (b) full test run green, (c) the exact paths
of every new file and the two scheduled jobs.
