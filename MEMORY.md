# MEMORY.md -- vlm_session_volume

## Session: 2026-06-17 (Phase 3a)

### What was built
New standalone repo `vlm_session_volume/` implementing session-window volume comparison
for CT options tape. Zero files written into Options_flow_analyzer/.

Files created:
- config.py          -- self-contained config, read-only path to old repo tapes
- contract_resolver.py -- vendored + extended generic resolver (H/K/N/Z all four months)
- session_volume.py  -- core logic: overnight/day windows, RVOL tiers, history, report
- tests/test_session_volume.py -- 63 hermetic tests, all passing
- MEMORY.md          -- this file

### Key decisions

**New repo, not old repo.**
Why: owner explicit -- "enough bunched into the analyzer folder." Zero collision risk.
Rejected: adding files to Options_flow_analyzer/. Not done.

**Self-contained config.**
Why: any import from old repo creates a coupling that breaks the zero-touch rule.
How: CT_SERIAL_TO_FUTURES, CT_EXCLUDED_PREFIXES, window times all copied verbatim.

**Permanent history -- never delete.**
Why: source tapes self-delete at 10 days; 20/30/60-session RVOL needs indefinite record.
File: data/history/session_volume_history.csv -- append-only forever.
Idempotent: re-running same date overwrites that row, never duplicates.

**Inclusive/exclusive cutoff semantics.**
Why: overnight must exclude the 07:00:19 snapshot (first of day session).
How: _window_cutoff(..., inclusive=False) -> ':00' for overnight end;
     _window_cutoff(..., inclusive=True)  -> ':59' for day start/end.
Bug caught in testing: using ':59' for overnight included 07:00:19 in overnight total.

**contract_resolver.py extends contract_calendar.py to all four months.**
Why: original is December-only. We need H/K/N/Z generics at positions 1 and 2.
How: vendored pure functions, extended resolve_generic() with calendar-locked logic.
Rejected: editing contract_calendar.py directly (would touch old repo).

**4-digit delivery year on every row.**
Why: ICE single-digit year recycles every decade; indefinite history needs 2026 not 6.
How: parse_ice_code() recovers full year from as-of date context.

**October (CTV) and August (CTQ) hard-excluded at read time.**
Matches existing repo frozenset CT_EXCLUDED_PREFIXES = {'CTV', 'CTQ'}.
Applied in _map_contract() before any other logic.

**Loud fail with exact file path.**
Any structural error (missing tape, unwritable history) exits non-zero with abs path.
One tolerated soft-skip: malformed/partial last row of live tape (WARNING + line number).

### Backtest results (2026-06-17, --no-write)
Jun 11: overnight 346 (CTN6+CTZ6), day 8,812
Jun 12: overnight 1,896 (CTN6+CTZ6), day 8,126
Jun 15: overnight 208 (CTZ6 only), day 4,711
Jun 16: overnight 209 (CTZ6+CTH7), day 4,501
Jun 17: overnight 546 (CTZ6) -- matches overnight_volume_history.csv exactly (546, call 256, put 290)

Note: Jun 11/12 tapes start at 05:30 (not 21:00) -- overnight data present but
pre-dates the full overnight window. Reported with WARNING.

### What was rejected
- Adding files to Options_flow_analyzer/ -- violates Phase 3 authorization
- Editing contract_calendar.py -- would touch old repo
- Using ':59' for overnight cutoff -- incorrectly included first day snapshot

### Files found in old repo from PRIOR session (not written this session)
Options_flow_analyzer/session_volume.py
Options_flow_analyzer/tests/test_session_volume.py
Options_flow_analyzer/data/history/session_volume_history.csv
Options_flow_analyzer/data/*/session_volume.{json,txt}
These predate Phase 3 authorization. Owner should delete them for clean separation.

### Next session priorities (Phase 3b)
1. Register two scheduled Windows tasks:
   Morning ~07:15 ET weekdays: python session_volume.py --window overnight
   EOD ~14:35 ET weekdays:     python session_volume.py --window both
2. Run in parallel with old overnight-options-volume task for 2-3 sessions
3. Verify history file accumulates correctly with real writes
4. Then Phase 3c: api.py VLM gateway endpoints

### Rollback plan
Delete vlm_session_volume/ + remove its two scheduled tasks + re-enable old morning task.
Zero existing files modified => rollback cannot affect existing pipeline.

## Session: 2026-06-17 (Futures Session-Volume — PART A: capture sidecar)

### What was built (in Options_flow_analyzer, NOT this repo)
price_tape.py gains a SIDECAR write only — a brand-new file
`data/<date>/ct_futures_volume.csv`. The price tape (_FIELDS), its dedup, backup,
and history are byte-for-byte unchanged. This is the analyzer's ONLY change.

Sidecar schema: timestamp, date, commodity, contract, boundary(open|0700|1420), volume, oi.

Cadence: forced boundary flush at the first poll on/after 21:00 / 07:00 / 14:20 ET
(naive ET — matches the existing loop's datetime.now() convention; machine clock is ET).
Three readings -> night = 0700-open, day = 1420-0700, full = 1420-open.

### Implementation notes
- New funcs in price_tape.py: _sidecar_path(), _write_sidecar(), _flush_boundary().
  Path built from settings.daily_data_dir() so NO edit to config/settings.py was needed.
- _flush_boundary does its own RTD read (the seam _read_workbook, test-patchable) and
  swallows all errors (logs SIDECAR_*): a capture failure can NEVER disturb the price tape.
- run() loop: boundaries_done set, reset on date roll; flush placed AFTER the settle-window
  guard so it never reads COM during 14:25-16:00 (14:20 boundary is safely before settle).
- Contracts with neither volume nor oi are skipped (no empty rows).

### Open question to confirm with LIVE data (do NOT assume)
Does futures CUMULATIVE volume reset at 21:00 (session open)? The three-reading design
does not assume it either way, but RECORD the finding here after one live session:
if open-reading volume ~= prior 1420 -> no reset (cumulative across sessions);
if open-reading volume ~= 0 -> resets at 21:00. TBD.

### PROOF GATE results (Part A stop-point)
- Full pytest: 495 passed, 0 failed (was 483; +12 new sidecar tests in test_price_tape.py).
- git diff: exactly 2 tracked files changed — price_tape.py + tests/test_price_tape.py,
  184 insertions(+), 0 deletions. No other analyzer file touched.
- _FIELDS (price-tape schema) byte-identical in diff (no +/- on that list).
- Only new data write target is ct_futures_volume.csv (sidecar). No write to tape/history/backup.
- Collision recheck (verified at source before coding):
  * volume @ ice_rtd_reader.py:334, oi @ :343 — already in the live RTD futures dict.
  * options_tape 10-day cleanup deletes a NAMED file only, not a glob — can't sweep sidecar.
  * GEX/synopsis globs target gex_output.json / *.json / *.png — never *_futures_volume.csv.
- Live smoke test: 3 boundaries x 2 contracts = 6 rows, correct schema, single header,
  price-tape file NOT created by the sidecar path.

STOPPED here for owner review per BUILD_futures_session_volume.md (Part A proof gate).
Nothing merged/committed. Part B (new-repo engine) not started.

### AMENDMENT (owner-approved): exact-minute -> on/after, absolute datetimes
Owner approved Part A with one change to the boundary capture:
- Switched from exact (hour, minute) tuple match to "first poll ON/AFTER" each boundary.
- Implemented with ABSOLUTE boundary datetimes built from the session date via
  _boundary_datetimes(date_str): open = prior-evening (date-1) 21:00; 0700 / 1420 on
  the session date. Fire when `now >= boundary_dt`, with the once-per-boundary latch.
- WHY absolute, not (h,m) tuples: the session wraps past midnight, so 21:00 >= 07:00 and
  21:00 >= 14:20 as TUPLES would wrongly fire the morning boundaries at the evening open.
  Absolute datetimes compare correctly. Proven: at 2026-05-25 21:00 (Tue-session open),
  only ['open'] is due; morning boundaries (10-17h in the future) do not misfire.
- Schema UNCHANGED — kept the 7 fields exactly (timestamp,date,commodity,contract,
  boundary,volume,oi). No staleness/reliable columns: the timestamp already shows how
  close to the boundary the reading landed, and volume is cumulative so a missed 20s poll
  loses nothing. The only thing on/after protects against is a multi-minute outage
  straddling a boundary (still logged via SIDECAR_SKIP if RTD is down).
- New funcs: _boundary_datetimes(); _SIDECAR_BOUNDARY_DEFS replaces _SIDECAR_BOUNDARIES.
- Tests added: open-is-prior-evening, no-morning-misfire-at-evening-open, and the
  outage test (no poll in the 07:00 minute -> first poll at 07:03 still writes 0700 once,
  latch holds on a later poll).

### PROOF GATE re-run (after amendment)
- Full pytest: 498 passed, 0 failed (+3 boundary tests over the prior 495).
- git diff: still exactly 2 tracked files — price_tape.py + tests/test_price_tape.py,
  265 insertions(+), 0 deletions. No other analyzer file touched.
- _FIELDS price-tape schema byte-identical (only _SIDECAR_FIELDS carries a +).
- Live smoke: evening-open poll yields due=['open'] only; 14:25 poll yields all three.

STILL STOPPED for owner review. Part B not started.

## Session: 2026-06-17 (Part B -- futures engine, Bloomberg-seed model)

### Data model CORRECTION applied (owner-directed, supersedes my first build)
First Part B build used oi_data.csv as the full source -- WRONG. oi_data `volume`
is low-vintage (~11 nonzero sessions only), which made deep RVOL tiers average in
zeros. Owner resolved it: full-session = Bloomberg PX_VOLUME, verified vs ICE
official daily report (12-Jun-2026): thin months exact, active fronts ~2-4%
(gap = ICE TAS/TIC vs bbg outright). oi_data NOT used as history source.

### LOCKED sourcing (built to this)
- FULL = Bloomberg PX_VOLUME. Deep history seeded from
  cotton_futures_volume_history.csv (project folder): 43,752 rows, 8 generics,
  2005-01-03 -> 2026-06-17, 33,208 nonzero-volume rows. No Oct/Aug in the file.
- NIGHT/DAY = sidecar SHARE applied to the authoritative bbg full:
  night = night_share*full, day = day_share*full, so night+day == full per
  contract (verified: CTDEC1 25,614.6+17,076.4 == 42,691.0). Forward-only.
  The sidecar's raw 14:20 cumulative is NEVER the full total.
- RVOL deep tiers now use REAL history (degrade only at the 2005 boundary).

### Files built
- futures_session_volume.py -- engine: seed loader, full_for_date, sidecar
  share -> apply_split, RVOL, 4 comparisons (night/day/full + night_share/
  night_day_ratio), permanent idempotent history (session + by-contract),
  holiday guard, loud-fail-with-path. CLI: --seed[/--no-write], --backtest,
  --date, --no-write.
- config.py -- added FUT_SEED_CSV, sidecar path, FUT_BOUNDARIES, history paths,
  bbg-source notes. oi_data kept only as recent-weeks cross-check.
- tests/test_futures_session_volume.py -- 21 hermetic tests (universe/Oct-Aug
  exclusion, non-Dec symbology + 4-digit year, split reconciliation night+day==
  full, sidecar share math, RVOL degradation, ratios, holiday guard, loud-fail-
  with-path, permanent+idempotent history, by-contract symbology key).
- Symbology: reused contract_resolver.py (already H/K/N/Z pos 1&2, 4-digit year,
  Oct/Aug exclusion) -- satisfies the doc's symbology.py requirement; not duped.

### Verification (--no-write backtest)
- full 2026-06-17 = 68,126 -- reproduces EXACTLY from raw seed.
- RVOL-5 avg 77,524 / RVOL-5 0.88x -- reproduce exactly. Tiers now sane (0.6-0.9x),
  no zero-averaging artifact.
- Oct/Aug absent from every output; delivery years resolve (CTDEC1->CTZ6/2026,
  CTMAR1->CTH7/2027).
- night+day==full split invariant holds per contract (synthetic sidecar test).
- New-repo suite 84 passed (63 options + 21 futures). Analyzer untouched
  (git diff: only price_tape.py + tests/test_price_tape.py).

### STOPPED at backtest stop-point (per BUILD doc Part B)
NOT written: permanent history (backtest was --no-write); NO jobs scheduled.
Awaiting owner sanity-check of the sample report (night/day/full math + symbology
vs tape) before --seed write and B-jobs.

### Open item still pending a live session
Confirm whether futures cumulative volume RESETS at 21:00 (record finding here).
Can't know until a live sidecar exists.

## Session: 2026-06-18 (Cross-repo coupling — consumer-side hardening)

### The coupling (from THIS repo's perspective)
This repo's forward-session input `ct_futures_volume.csv` is PRODUCED BY a SEPARATE
project: `Options_flow_analyzer/price_tape.py::_write_sidecar` (Part A). This repo
is the CONSUMER — it only READS that file (futures_session_volume.py::
read_sidecar_direct, via config.futures_sidecar_path()). It does NOT write it.

### Who owns the contract
The PRODUCER owns the sidecar schema. The source of truth is
`Options_flow_analyzer/SIDECAR_CONTRACT.md` — the 7 columns, in order:
`timestamp, date, commodity, contract, boundary, volume, oi`.
This repo ADAPTS to that schema; it must never assume authority over it.

### The risk
The link is by ABSOLUTE PATH with NO import — nothing in Python ties the two repos
together. So a producer schema change (renamed/dropped column, reordered output)
would not raise an ImportError or any signal; it would just make this consumer
read garbage or skip rows SILENTLY. That is the exact failure mode to prevent.

### Mitigation (built this session — see test/guard below)
read_sidecar_direct now runs a read-time schema-validation guard ONCE, right after
the csv.DictReader is created and before the row loop:
- Module constant `_SIDECAR_EXPECTED_COLS` = the 7 contract columns.
- If `reader.fieldnames` is None, or any expected column is MISSING, it raises
  ValueError naming the sidecar ABSOLUTE PATH, the missing column(s), and the
  actual columns found. Loud + diagnosable on column drift.
- EXTRA columns are TOLERATED (additive producer changes are allowed by the
  contract). Only MISSING expected columns fail.
- Behavioral preservation: a file that does NOT EXIST still returns None (soft
  skip — sidecar not produced yet). The raise is ONLY for a file that exists but
  has the wrong columns.

### Tests added (tests/test_futures_session_volume.py)
- test_sidecar_missing_required_column_raises — drops `volume`, asserts ValueError
  mentioning the missing column and the path.
- test_sidecar_extra_column_tolerated — 7 cols + an extra field, asserts no raise
  and correct math (additive tolerance).
No existing test weakened; the existing `_write_sidecar` helper already writes all
7 columns, so the guard left the prior suite green.

### What was NOT touched
Producer (Options_flow_analyzer) untouched. No computation/RVOL/history/path
changes. Only: this MEMORY note, the read-time guard, and the 2 tests.

---

## 2026-09-02 — Price overlay gaps + the FND roll defect (Opus 5)

Started as "why is there no price data on most of the dates" on the session
volume chart. Ended up closing three distinct defects, two of them older than
the reported symptom.

### Worked on / Completed

**1. Price overlay — 88 of 173 sessions priced, now 167 of 167.**
`api/price.py` read settle only from the ICE eod capture root, which begins
2026-04-27, so everything before it drew nothing.

The first fix (Bloomberg as a pre-capture *backfill*, ICE as authority) was
WRONG and Lou caught it: "ice keeps time and sales for only so long... if you
need hi lo close volume etc, there are decades of it." The ICE eod root is a
TIME AND SALES capture with a short retention window — 37 of its 92 CT
day-folders carry `_BACKFILL.txt`, reconstructed 2026-07-06, and a
reconstruction only sees contracts still listed the day it runs. That is why
N26/K26 appear in no May/June folder and the front month drew blank for two
months. Settle/OHLC/volume is decades-deep reference data with no retention
limit.

Repointed: **Bloomberg leads, ICE fills only what Bloomberg has not published
yet** (today's session). Costs nothing in fidelity — CTZ26 settle matched
Bloomberg CTDEC1 px_last within 0.005 on **87 of 87** ICE day-folders (50 live,
37 backfilled, zero disagreements).

**2. Contract selector.** Multi-select was already functional but looked
single-select (`size=3`, no affordance). Widened to 7 with a ctrl+click hint.
Multi-contract selections previously dropped the price line entirely; they now
show front month labelled as a reference, not a claim.

**3. The FND roll defect (the real find).** `contract_resolver` rolled a generic
at `date(year, delivery_month, 1)` while its own header comment claimed
first-notice. Contracts leave the board at FIRST NOTICE DAY, ~5 business days
earlier, so for ~6 sessions per roll the front-month generic named a dead
contract. Not cosmetic: `ice_to_generic` stamps `generic_code` INTO the archive.

New `expiry_source.py` sources FND from the ruled authorities (gateway → local
`expiry_master.csv` → vendored historical for expired contracts neither
retains). Holds no expiry facts of its own for a listed contract, surfaces each
authority's real content age rather than trusting a `stale` flag, and raises
rather than guessing.

**4. Archive backfill APPLIED** — 47,329 rows across bar5m/minute_agg/ticks,
plus 402 rows in the by-contract sidecar CSV.

### Decisions made

- **Bloomberg is the PRICE authority; ICE is the TAPE authority.** Lou ruled
  ICE Settle vs Bloomberg px_last close enough for a graphical overlay, so no
  per-point source labelling on the chart — but the API still returns `source`
  so the distinction is never lost.
- **The Bloomberg session gate stays per-SESSION, not per-contract.** Relaxing
  it to fill any contract absent from an ICE file was tried and REVERTED: a
  blank/'N/A' Settle is "absent" by the same test, so it reopens a silent
  vendor swap on a session ICE did cover. The incomplete-capture gap belongs
  upstream in the capture, not in a price-layer exception that cannot tell the
  cases apart. Rationale recorded in the code.
- **Lou's shape rule** (FND = 5 business days before the 1st business day of the
  delivery month) is a **CROSS-CHECK, NEVER A SOURCE**. It agrees on 4 of 5
  checked contracts and misses CTZ26 by a day (Thanksgiving). Business-day math
  cannot know which holidays ICE observed —
  `EXPIRY_AUTHORITY_ACCESS_PROTOCOL.md` §5 forbids deriving a contract date
  from calendar arithmetic.
- **Vendored historical FNDs are append-only and must be SOURCED**, never
  computed. Nine contracts vendored (CT H26/K26/N26/Z25/V25, CCK26, KCK26,
  SBK26, SBN26) from Bloomberg `FUT_NOTICE_FIRST`, each cross-validated against
  the live gateway on contracts both carry — exact on all.
- **The local SQLite is a stale CT-only seed. The live store is Supabase**
  (`DATABASE_URL`, set at OS level) and carries KC/CC/SB as well. Measure scope
  against the live store, never the seed.

### Rejected

- ICE-as-price-authority (the original design) — retention window makes it
  structurally unable to answer for expired months.
- Widening the unknown-contract fallback to keep Dec-2025 rows resolving —
  solved by sourcing CTZ25's real FND instead of loosening a safety rule.

### Gotchas found (see ERRORS.md)

- SQL placeholders are `%s` ALWAYS; `store/db.py::_sql` translates DOWN to `?`
  for SQLite. Writing `?` passes a SQLite sandbox and dies on live Postgres.
- SB genuinely has **FND after LTD** (SBK26 fnd 2026-05-01, ltd 2026-04-30) and
  12 SB contracts have FND on/after the 1st of the delivery month. No rule may
  assume FND precedes either.
- The expiry cache must be keyed PER COMMODITY or the first commodity asked
  poisons every other one.

### Next session priorities

1. **Verify the expiry refresh fired Friday 2026-09-04** ("VLM ICE Expiry
   Refresh", desk machine, 07:30 ET). As of 2026-09-02 the gateway read
   `refreshed_at: 2026-08-03` — 30 days against an 8-day check interval, with
   `stale: false`. Per protocol §5 a status field is not evidence; check
   `vlm_cal_expiry_freshness` or the task's actual last-run.
2. Re-run `jobs/backfill_generic_code.py` after ANY future resolver change — it
   repairs the DB and the sidecar in one pass and is idempotent.
3. The ICE capture's incomplete May/June folders (missing live front months)
   remain an upstream capture issue, unfixed by design.

---

## Session 2026-09-09 -- Bloomberg CT seed CSV now tracked for the gateway

### What was decided

- **`cotton_futures_volume_history.csv` is now committed to the repo**, not
  gitignored. It was excluded under a "large regenerable Bloomberg seed"
  comment, but nothing regenerates it in practice: the refresh task
  (`VLM_CT_FutVol_SeedRefresh`) is Disabled and has never run, and its script
  (`refresh_seed.py`) points at a non-existent engine path (see Gotchas
  below) -- so an untracked file was the ONLY copy, unreachable by the VLM
  gateway. Committed as-is: 44,192 rows, 2005-01-03 to 2026-09-02, 8 CT
  generics, no secrets found on inspection. `.gitignore`'s comment was
  reworded (not removed) to record that the seed is committed intentionally
  and that future reseeds should use `cotton_futures_volume_history_blpapi.py
  --merge` (upsert) rather than a full rewrite, to keep diffs small.
- **`ice_timesales_engine/api/price.py` and `futures_session_volume.py` keep
  reading the local file unchanged.** This was a visibility fix (gateway can
  now serve the seed from the repo), not a pipeline change -- no code touched,
  no reader's path or read behavior altered.

### Why

- The gateway can only serve what's in the repo. The seed was the
  authoritative Bloomberg source for CT settle/volume/OI (price.py's
  documented price authority) but was invisible to anything reading off
  GitHub rather than the local disk.

### Rejected

- **Enabling `VLM_CT_FutVol_SeedRefresh` or fixing `refresh_seed.py`** -- out
  of scope for this fix; noted as an open bug for Lou, not touched.
- **Committing the two stray untracked root files** (`New Text Document.txt`,
  `desktop.ini`) -- unrelated to this fix, left untracked.

### Observations for Lou (not acted on)

- **`VLM_Session_Volume_CaptureCheck`'s last scheduled run (2026-09-08 14:40
  ET) returned exit code 2 (non-zero)**, per `schtasks /Query`. Worth
  checking the capture-completeness alert it's meant to raise.
- **`refresh_seed.py` hardcodes `_REPO = Path(r'...\Desktop\vlm_session_volume')`**
  (note the lowercase/underscore name) for the engine path used in Step 2 --
  that directory does not exist on this machine; only
  `VLM_Session_Volume_Project` does. The script would fail at Step 2 even if
  the task were re-enabled. Not fixed here; scope was the seed CSV only.
- **`VLM_Session_Volume_Morning` is not a registered scheduled task at all**
  (confirmed via a full `VLM*` task-name scan) -- `register_and_cleanup_tasks.ps1`
  deliberately leaves it commented out ("interim no-op until the 14:20
  boundary exists"). Two of the three originally-assumed live tasks are
  actually live: `VLM_Session_Volume_EOD` and `VLM_Session_Volume_CaptureCheck`,
  both confirmed Ready/Enabled after this session's changes; Morning is
  intentionally off, not broken.

---

## Session 2026-09-09 (cont.) -- CaptureCheck diagnosed (upstream, not fixed here); refresh_seed.py repointed

### Part 1 -- `VLM_Session_Volume_CaptureCheck` exit 2: diagnosed, NOT a bug in this repo

**Verdict: exit 2 is correct and has fired every trading day since 2026-07-02.**
Not a one-off, not an unhandled exception (those exit 3 per the script's own
`except Exception` handler).

- `schtasks /Query /TN VLM_Session_Volume_CaptureCheck /FO LIST /V`: Last Result
  `2`, Enabled/Ready, action = `check_capture.py --eod --commodity CT`, working
  dir = this repo root. `Get-WinEvent` TaskScheduler-Operational confirms the
  same run: event 201 logs return code `2147942402` (0x80070002, Task
  Scheduler's HRESULT wrapper for plain exit code 2), launched by a time
  trigger, completed normally (not crashed).
- `check_capture.py` exit codes are self-documented and deliberate: `0` = OK
  or market closed, `2` = INCOMPLETE capture (loud alert), `3` = config/usage
  error. Exit 2 fires when `<OPTIONS_FLOW_DATA>/<date>/ct_futures_volume.csv`
  is missing or a due boundary has zero universe contracts.
- Checked the actual sidecar source (`Options_flow_analyzer/data/<date>/
  ct_futures_volume.csv`, `config.OPTIONS_FLOW_DATA`): present and populated
  daily from 2026-06-18 through **2026-07-02**, then **absent on every single
  trading day since** (checked all 20 most recent date-folders through
  2026-09-08 -- zero hits; 2026-09-09 has no date-folder at all yet).
- Root cause: **`Options_flow_analyzer/price_tape.py` no longer exists in that
  repo** (`ls` confirms; also confirmed via `grep -rl` that no live file in
  that repo still writes `_write_sidecar`/`_flush_boundary`/
  `ct_futures_volume.csv` -- only historical `ct_price_tape_status.txt`
  artifacts from the same June/July window remain). That repo's own
  `ICE_TIMESALES_ENGINE_BUILD_PLAN.md` and this repo's 2026-09-02 MEMORY entry
  confirm the producer was migrated to `ice_timesales_engine` (Supabase-backed)
  around the same time -- `price_tape.py`'s CSV-sidecar mechanism was retired,
  not repaired in place.
- Confirmed this is exactly the silent-failure `check_capture.py` exists to
  catch: ran `futures_session_volume.py --window eod --commodity CT` by hand
  for 2026-09-09 -- it exited 0 and printed `WARNING: no RTD sidecar and no
  Bloomberg seed data for 2026-09-09 -- nothing to report` / `No data for this
  session.` The EOD engine's own "Last Result: 0" in `schtasks` is therefore
  NOT proof of a healthy pipeline -- it is silently producing empty reports,
  and CaptureCheck's exit 2 is the only signal saying so.

**No code changed for Part 1.** This is a genuine upstream capture gap, not a
bug in `check_capture.py`, and repointing its source (CSV sidecar path/schema
-> whatever `ice_timesales_engine` now produces) is a cross-repo architecture
decision -- squarely the CLAUDE.md blast-radius stop rule, not a same-repo
smallest-safe-fix. Reported to Lou below rather than guessed.

### Part 2 -- `refresh_seed.py` repointed; task left Disabled

- Confirmed by full read: Step 1 runs `cotton_futures_volume_history_blpapi.py
  --start <start> --end <end> --output <seed_csv> --merge` (trailing N calendar
  days, default 15) to upsert Bloomberg's `PX_VOLUME`/OHLC/OI into
  `cotton_futures_volume_history.csv`. Step 2 re-runs `futures_session_volume.py
  --commodity CT --window eod --date <yesterday>` so yesterday's history row
  moves from RTD-sidecar/intraday estimate to Bloomberg-final. `--window eod`
  is a valid choice (`choices=['overnight','eod']` in
  `futures_session_volume.py`) -- only the docstring's "final" wording is
  stale, not the code.
- Consumers of the seed CSV, verified by grep: `futures_session_volume.py`
  (`config.FUT_SEED_CSV`, RVOL seed) and `ice_timesales_engine/api/price.py`
  (Bloomberg-first settle authority, reads this repo's own copy via
  `ice_timesales_engine/config.py`'s `VLM_SESSION_VOLUME_REPO` path -- same
  file, not a duplicate). Gateway route `/v1/ct/generics/history` was not
  found as a literal string anywhere in this repo (grep for
  `generics/history` empty) -- it's served by the separate gateway codebase
  off the committed CSV (see 2026-09-09 seed-CSV-tracking entry above), not by
  code in this repo.
- **Fix:** `_REPO` was `Path(r'...\Desktop\vlm_session_volume')` (dead,
  lowercase/underscore, does not exist). Changed to `_REPO = _HERE` (the
  existing `__file__`-relative constant already used for `PULLER`/`SEED_CSV`),
  so `ENGINE` and `LOG_DIR` now resolve inside this repo. One-line diff.
- Bloomberg terminal must be logged in for Step 1 (`cotton_futures_volume_
  history_blpapi.py` does `import blpapi` and opens a live `blpapi.Session`) --
  it will fail without an active terminal session, independent of this fix.
- Proof: `python refresh_seed.py --dry-run` now prints both step commands with
  paths inside `VLM_Session_Volume_Project` and does not error. `python -c
  "import refresh_seed"` succeeds; `refresh_seed.ENGINE.exists()` is `True`.
  Did NOT run Step 1 for real (no Bloomberg pull) and did NOT enable
  `VLM_CT_FutVol_SeedRefresh` (confirmed still `Disabled`, Last Run Time is
  the never-run sentinel) -- both explicitly reserved for Lou.
- Full `pytest tests/` still 110 passed, 0 failed after the change.

### Recommendation for Lou (not acted on)

Leave `VLM_CT_FutVol_SeedRefresh` disabled for now. The seed CSV's daily
forward path was never Bloomberg in this design (`BUILD_futures_session_
volume.md`: "Bloomberg = ONE-TIME reseed... NOT a daily job"; ICE RTD sidecar
was meant to be the daily forward source) -- and that RTD sidecar is the same
thing Part 1 found broken since 2026-07-02. Enabling the 09:00 task today
would run Step 1 fine (assuming Bloomberg is logged in) but Step 2 would keep
re-confirming empty/estimate rows, because the sidecar it's finalizing FROM
doesn't exist. Fixing the sidecar producer (Part 1) is the actual prerequisite
-- enabling this task first just adds a second daily Bloomberg pull without
solving the forward-data gap. Once the sidecar (or its `ice_timesales_engine`
successor) is repointed and confirmed live, re-evaluate: enabling gives a
same-day Bloomberg-final overwrite of yesterday's estimate (keeps the seed CSV
and the gateway's `/v1/ct/generics/history` current to yesterday's settled
volume instead of an RTD-derived estimate); leaving it disabled means the seed
stays at its last manual reseed and the gateway route serves whatever vintage
that is, with no automatic drift alarm of its own.

## 2026-09-09 (later) — old session-volume tool retired; refresh_seed repointed

**Decided (Lou): retire.** `VLM_Session_Volume_CaptureCheck` exit 2 was correct every day since 2026-07-02: the RTD sidecar (`Options_flow_analyzer/price_tape.py`) it monitored was retired when the ice_timesales_engine took over, so `futures_session_volume.py` produced nothing while its EOD task exited 0. The engine already computes night/day/full windows from the tick feed and the gateway serves them. Deleted tasks `VLM_Session_Volume_EOD` and `VLM_Session_Volume_CaptureCheck`; scripts kept with a RETIRED header. Rejected: re-sourcing the old tool from the engine's bars (rebuilds what the engine does).
**refresh_seed.py** now resolves `_REPO` relative to its own file = this project folder (confirmed by --dry-run: seed CSV and engine paths both under VLM_Session_Volume_Project). Task `VLM_CT_FutVol_SeedRefresh` stays Disabled; step 2 (engine finalize) is moot now. If Lou wants the Bloomberg generic series (`/v1/ct/generics/history`) current daily, strip step 2 and enable at 09:00 weekdays with the terminal logged in.

---

## Session 2026-09-20 — CC/SB 2026-09-18 re-ingested; the capture/ingest race fixed at the ingest; CT seed reseeded

### 1. CC/SB 2026-09-18 backfilled (Supabase write, authorised)

**What.** Ran `python -m jobs.daily_ingest --commodity SB --date 2026-09-18`
then the same for CC, from `ice_timesales_engine`. SB: 11 files, 40,869 rows.
CC: 9 files, 41,054 rows. Both rolled up to `minute_agg`, `bar5m`, reconcile
flags and the sidecar history CSVs.

**Why.** The 17:10 ET ingest beat the 17:00 softs capture loop (KC -> SB -> CC)
to the disk on 09-18: CC's capture finished 17:29, SB needed a manual rerun
that landed 20:13. Both logged `no blotter files -- zero volume day (by
design)` and exited 0, so nothing ever went back for them. The blotter files
were on disk intact the whole time — this was a pure re-ingest, no re-capture.

**Verified by EFFECT, not exit code.** Before: ticks 09-18 held CT 37,716 and
KC 17,279 only. After: CC 41,054, CT 37,716, KC 17,279, SB 40,869 — CT and KC
byte-identical, in ticks, `minute_agg` (CT 5,408 / KC 3,445) and `bar5m`
(CT 2,141 / KC 1,420), and their sidecar rows kept their original 09-18
timestamps. Gateway confirms live: `/v1/sessionvolume/{CC,SB}/bar5m?session_
date=2026-09-18&source=ice` both return rows, and `/v1/sessionvolume/sessions`
now lists a 2026-09-18 ice session for both.

### 2. The race fixed at the ingest, not by moving a clock

**What.** Three changes in `ice_timesales_engine`:

- `ingest/discover.py` — new `capture_landed(commodity, session_date)`. The
  day FOLDER is the discriminator: the capture creates it and writes settle /
  spreads / settled_surface into it, so a folder present with artifacts means
  the capture ran (a zero blotter count is then a real no-trade day), while a
  missing or empty folder means it has not landed.
- `jobs/daily_ingest.py` — the "no blotter files" branch now splits. Capture
  landed -> `no_blotter`, exit 0, unchanged by-design behaviour. Capture NOT
  landed -> status `capture_pending`, a distinct log line `capture not landed
  yet`, and `main()` returns **3** so the batch wrapper logs `[FAIL]` for that
  commodity and Task Scheduler's Last Result stops reading green. 3 rather
  than 1 so it is distinguishable from a genuine ingest error.
- `jobs/catchup_ingest.py` (new) — second pass. `select_catchup_days()` is a
  pure function (filesystem listing + set of DB keys in, work list out) so the
  selection rule is testable without a DB, a disk or a network. It ingests any
  (commodity, date) in the last N trading days whose blotter folder HAS files
  but which has no `bar5m` rows. `--days`, `--commodity`, `--dry-run`.

**Why `bar5m` and not `ticks` as the "already done" key.** `bar5m` is what the
gateway serves and it is rebuilt delete-and-reinsert per day, so its presence
means the whole pipeline ran, not merely that some ticks landed.

**Anti-spin guarantee.** A day with zero blotter files is never selected,
whatever the DB says. That is what stops the job re-running forever on a
genuine zero-volume or holiday-adjacent day such as 2026-07-03, whose folders
really do hold settle/settled_surface files, really do have zero futures
blotters, and really do have no DB rows — permanently and correctly.

**Test.** `tests/test_catchup_selection.py`, 14 tests, hermetic. The fixture is
deliberately the unfavourable one: it contains the holiday-adjacent genuine
zero-volume day alongside the 09-18 incident, so a rule keyed on "DB has no
rows" alone would pick the holiday every run. Sabotage-verified twice — with
the file-count guard replaced by `if False:` two tests go red, and a dedicated
test feeds the same function the same fixture with only the blotter counts
falsified and asserts the holiday IS then selected, proving the other
assertions exercise the guard rather than agreeing with the fixture.

`tests/test_idempotency.py::test_no_blotter_day_is_zero_not_failure` was
renamed and its expectation changed — it used a MISSING day folder as its
stand-in for a zero-volume day, which is exactly the conflation being fixed.
It now pins `capture_pending`, and a new sibling test pins the unchanged
`no_blotter` branch with a landed capture. Full suite: **209 passed, 17
skipped**.

### 3. Five MORE gaps of the same class found (NOT fixed — needs authorisation)

`python -m jobs.catchup_ingest --days 40 --dry-run` found, beyond 09-18:
**CC 2026-08-18, CC 2026-08-27, SB 2026-08-18, SB 2026-08-26, SB 2026-08-27** —
blotter files on disk, zero `bar5m` rows. Verified directly against the DB.
So 09-18 was not a one-off; this race has been eating CC/SB sessions for a
month. Write authorisation for this session covered the 09-18 re-ingest only,
so these were left alone. `python -m jobs.catchup_ingest --days 40` fixes all
five in one pass once Lou approves.

### 4. CT Bloomberg seed reseeded (task 2)

**What.** `python refresh_seed.py --days 25` (25 not the default 15, because
the gap was 09-02 -> 09-20, 18 days — the default window would not have
reached it). Bloomberg reachable (localhost:8194 open). 144 rows merged,
44,144 kept, 44,193 -> 44,289 lines. New max date **2026-09-18** (was
2026-09-02); 12 new session dates added. All 8 generics on the max date carry
non-blank settle and volume.

**Nothing truncated:** zero (date, generic) keys lost against a pre-run backup;
min date still 2005-01-03.

**CORRECTION (audit, same session). 2026-09-02 was not "blanks filled in" —
it was a PARTIAL INTRADAY SESSION REVISED TO FINAL, and the first write-up of
this understated it.** All 8 generics had volume AND px_last revised
populated -> different on that date:

| generic | volume before -> after | px_last before -> after |
|---|---|---|
| CTDEC1 | 28,114 -> **59,274** | 88.89 -> 88.93 |
| CTDEC2 | 1,045 -> 3,662 | 79.35 -> 80.20 |
| CTJUL1 | 1,401 -> 4,277 | 91.75 -> 92.23 |
| CTMAR1 | 10,515 -> **26,087** | 91.03 -> 91.30 |
| CTMAR2 | 31 -> 83 | (blank) -> 80.62 |
| CTMAY1 | 3,135 -> **10,045** | 92.29 -> 92.77 |
| CTMAY2 | 0 -> 5 | (blank) -> 80.70 |
| CTJUL2 | 0 -> (blank) | (blank) -> 80.24 |

CTDEC1's volume more than doubled. **Why:** 2026-09-02 was the LAST row in the
file, i.e. the session the seed captured ON THE DAY it was last run — so it
held a mid-session snapshot, not the settled total. Bloomberg has now returned
the final figures for it. Confirmed as exactly that and nothing wider: the
other two dates in the overlap window, 08-31 and 09-01, have **identical
volume and px_last before and after** (e.g. 08-31 CTDEC1 33,727/93.14
unchanged; 09-01 CTDEC1 35,433/91.55 unchanged) — only their `open_int` was
filled where previously blank. A settled session re-pulls identically; only
the intraday one moved.

**Consequence worth keeping in mind:** any analysis run off this file between
09-02 and today was using a partial 09-02 (CTDEC1 short by 31,160 lots — the
old figure was only 47.4% of the true total, i.e. understated by 52.6%).
The file is now correct for that date. This is the generic hazard of a
manual-reseed file whose last row may be an intraday snapshot — the tail row
is provisional until the next reseed re-pulls it.

`open_int` was also filled in across the overlap where it had been blank.
`efp_volume`/`efs_volume` going blank on those rows is not a regression:
42,009 of 44,288 rows in the file already have them blank.

**Noted, pre-existing, not a defect:** 2026-09-07 (Labor Day) came back from
Bloomberg as OI-only rows with blank volume and settle. The file has carried
568 such rows across every historical ICE holiday since 2024 — this is how
Bloomberg has always returned a closed session here, not something this reseed
introduced.

CSV left **modified and uncommitted**, as instructed. Step 2 of `refresh_seed`
ran the RETIRED `futures_session_volume.py` and printed `no RTD sidecar ...
nothing to report` — the expected no-op recorded in the 2026-09-09 entry, not
a failure.

### Decisions

- **Decided:** fix the race in the INGEST (make it refuse to record a final
  zero it cannot justify) plus a catch-up pass, rather than move or add a
  capture-time clock.
  **Rejected — moving the 17:10 trigger later:** the next slow capture moves
  past the new time too. 09-18's own SB rerun landed at 20:13; no fixed clock
  survives that.
  **Rejected — a capture-completion marker file:** it would require changing
  the capture repo (`C:\Ice eod records`), a cross-repo write and a blast-radius
  stop. The day folder is already a marker the capture writes as a side effect.
  **Rejected — keying the catch-up on "DB has no rows" alone:** it spins
  forever on genuine zero-volume days. Hence the blotter-file-count guard.
- **Decided:** `capture_pending` exits 3, deliberately loud. The whole failure
  mode was a silent green.
- **Not done, on purpose:** nothing committed or pushed; no scheduled task
  created, modified or enabled; the five August gaps left for Lou.

### Next session

1. Approve `python -m jobs.catchup_ingest --days 40` for the five August gaps.
2. Decide on the drafted 21:00 ET catch-up trigger (block in the session
   report; NOT registered).
3. Decide whether to re-enable `VLM_CT_FutVol_SeedRefresh` (still Disabled;
   Mon-Fri 09:00 ET, runs `refresh_seed.py` with NO args = a 15-day window,
   and its step 2 is the retired engine).

### 5. Follow-ups same session — catch-up wrapper .bat + refresh_seed --seed-only

**`ice_timesales_engine/Run_Catchup_Ingest.bat` (new).** Sibling of
`run_daily_ingest_all.bat`, mirroring it exactly: same `DATABASE_URL` guard
(refuses to run rather than silently fall back to local SQLite), same
`py -3.14` pin, same `cd /d "%~dp0"`, same `logs\<name>.log` tee via the
`:log` helper. Runs `py -3.14 -m jobs.catchup_ingest --days 5` and exits with
the script's own code.

**Deliberately NOT mirrored: the trading-calendar gate.**
`run_daily_ingest_all.bat` needs `is_trading_day.py` because it omits `--date`
and would re-process Friday on a Saturday. The catch-up takes an explicit
window and `select_catchup_days()` already excludes closed dates and
zero-blotter days, so a weekend run is a clean no-op — and gating it out would
suppress exactly the Friday-evening catch-up this exists to perform.

Verified by running it, not by reading it: clean run exits 0 and logs
`[ OK ] catchup_ingest` with the window line teed into
`logs/run_catchup_ingest.log`; a scratch copy forced to exit 7 propagates
**7** and logs `[FAIL] catchup_ingest returned 7`; blanking `DATABASE_URL` for
a child cmd exits **1** with the guard message. (The `endlocal & exit /b %RC%`
late-expansion concern was tested rather than assumed — it propagates
correctly.)

**`refresh_seed.py --seed-only` (new flag).** Runs Step 1 and skips the
retired Step 2 entirely, printing one explicit SKIPPED line instead of the
`no RTD sidecar ... nothing to report` WARNING. Lets
`VLM_CT_FutVol_SeedRefresh` be re-enabled clean. **Default behaviour is
unchanged** — no flag still runs both steps.

`tests/test_refresh_seed_seed_only.py` (new, 8 tests, hermetic — `subprocess.run`
stubbed so no Bloomberg call and no file touched): asserts `--seed-only` runs
exactly one command and it is Step 1; that it still carries `--merge` (losing
it would overwrite rather than upsert a 44k-row file — silent truncation of 20
years); that a failed Step 1 still returns non-zero rather than a clean 0 just
because Step 2 was skipped; and that the default path still runs BOTH steps.
Sabotage-verified: replacing `if args.seed_only:` with `if False:` turns the
skip test red. Root suite now **118 passed** (was 110), engine **209 passed**.

**Rejected — deleting Step 2 outright:** it is still the documented path for
whenever the forward pipeline is re-sourced, and removing it changes default
behaviour for an interactive run. A flag leaves the default alone.
**Rejected — having the .bat pass `--days 5` via a task argument:** the
existing Daily Ingest task passes no arguments and keeps its parameters in the
.bat; matching that keeps one place to look.

**SeedRefresh task should carry:** Execute `...python.exe`, Arguments
`"...\refresh_seed.py" --days 25 --seed-only`. 25 not 15 because the default
window is too narrow to recover a gap like 09-02 -> 09-18. Still NOT enabled.

### 6. Audit fixes (same session)

**`capture_landed()` tightened — it was too weak to do its job.** As first
written it was `isdir and any(listdir)`, so a folder holding one settle file
while the blotters were still streaming read as LANDED, and `daily_ingest`
would have recorded a permanent `no_blotter` and exited 0 — reintroducing the
exact silent-zero the discriminator exists to prevent, through a narrower
window. Now requires all three: folder exists, >=1 file, and the **newest**
file's mtime is at least `CAPTURE_QUIESCE_SECONDS` (15 min) old. `now` is
injectable so the rule is testable without sleeping.

Newest, not oldest, deliberately: an old settle file beside a blotter written
seconds ago is an active capture, and keying on the oldest would call it
landed.

**Residual window, recorded not hidden:** a capture that stalls >15 min
mid-run and then resumes still reads as landed during the stall. That gap is
bounded and self-healing — `catchup_ingest` re-checks the last N trading days
for blotters-on-disk-but-not-in-DB, so such a day is picked up next pass
rather than lost. Closing it completely needs a completion marker written by
the capture itself, which lives in `C:\Ice eod records` — a cross-repo change,
deliberately not made.

Tests extended (engine **213 passed**, was 209): newest file 2 min old ->
`capture_pending`; 20 min old with zero blotters -> `no_blotter`; newest-
governs-not-oldest; both sides of the exact threshold. Sabotage-verified —
replacing the quiescence return with `True` turns **4** tests red, and the
sabotage run prints `no blotter files -- zero volume day (by design)` against
a mid-capture folder, which is the regression itself. Root suite 118 passed.

Verified against real disk after the change: all four 09-18 folders (long
quiet) read landed with their blotter counts intact, and 2026-07-03 reads
landed-with-zero-blotters, i.e. a correct final zero day.

**Rejected — a shorter quiesce window (e.g. 5 min):** the 09-18 softs loop had
~12 min between KC finishing and CC finishing; 5 min would have called the
folder quiet mid-loop. 15 min clears the observed inter-commodity gap.
**Rejected — waiting/polling inside daily_ingest until quiet:** it would hold
the scheduled run open for an unbounded time; reporting `capture_pending` and
letting the catch-up pass handle it keeps every run bounded.

## 2026-09-20 — config.CT_CLOSED_DATES extended to 2027

The closed-date set stopped at 2026-12-25, so from January every 2027 ICE holiday would have read as
a trading day (the ingest and catch-up gate on it). Added the ten 2027 softs closures from the ICE
2027 Trading Holiday Calendar notice (dated 2026-06-04), SOFTS column — including Mon 2027-01-18
(MLK). Mon 2028-01-03 is OPEN for softs and deliberately absent (it is Canola that closes). The
capture repo's own copy had transcribed the Canola column; see its MEMORY for the same date.
Still open: this is a second hand-kept calendar. The sandbox `ice_calendar` + `data/ice_holidays.json`
is the hot-reloading authority; importing or asserting against it would end the drift.

## 2026-09-20 - Daily Ingest moved from 17:10 to 17:45 (Lou approved)

What: Task Scheduler task "VLM ICE Timesales Engine - Daily Ingest" trigger changed 17:10 -> 17:45 (read back: 17:45; the 15:00 ingest and the 21:00 catch-up are unchanged).
Why: the softs blotter starts 17:00 and runs KC then SB then CC as three processes. Since the get_timesales batch was correctly cut to 10 symbols (2026-09-18) the run takes about 30 minutes, not 7 to 11: on 2026-09-18 SB started 17:09:23 and CC 17:17:27, so the 17:10 ingest fired before sugar and cocoa had been captured. The 21:00 catch-up and the capture_landed() quiescence check covered it, but the 17:10 slot did no useful work for SB/CC.
Rejected: moving the softs blotter earlier (it shares one ICE session with cotton's settle window; the 2026-08-27 KC Z26 loss is the precedent).
Note: 17:45 still precedes a worst-case softs run with full re-ask budgets (up to about 18:00); the 21:00 catch-up remains the backstop, and the capture manifests (Phase 1 softs, not yet landed) will let the ingest key on a finished leg instead of a clock.
