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
