# VLM Session-Volume Project — Master Registry

**Purpose:** single source of truth for this project — every method, path, and source the
application uses. Keep it current: whenever code, data, schedules, or sources change, update the
relevant table here and add a line to the Changelog at the bottom.

- **Created:** 2026-06-17
- **Last updated:** 2026-06-17
- **Owner:** Lou (trading@vlmsofts.com)
- **Status:** FUTURES-contract volume. Design LOCKED — sidecar capture (`ct_futures_volume.csv`) + 3 windows + 4 over-time comparisons. Blast radius verified CLEAR three ways. **Awaiting owner's verbal all-systems-go before any code.**

> Update policy: this file is the index. When the analyzer (or anyone) changes a method, adds a
> path, or wires a new source, edit the matching row and append to the Changelog. Treat an
> out-of-date row as a bug.

---

## 1. What this project is

A **session-window options-volume comparison** for ICE Cotton No. 2 (CT), built on the existing
Options Flow Analyzer's RTD tape. Two windows per session — **overnight 21:00→07:00 ET** and
**day 07:00→14:20 ET** — compared to trailing **5/10/20/30/60** trading sessions (RVOL), with a
**permanent, never-deleted** history for long-timeline (multi-year) comparison.

It is being built as a **separate new repo** (zero-collision with the existing app) and will be
exposed via the **VLM Data Gateway** API. See `PHASE3_AUTHORIZATION_session_volume.md` in this
folder for the full, locked spec.

### Hard rules (non-negotiable)
- **October (`CTV`) and August (`CTQ`) are never considered in any calculation.**
- **Capture universe = Dec/Mar/May/Jul futures months, 1st & 2nd generic only** → 8 slots:
  `DEC1/2, MAR1/2, MAY1/2, JUL1/2`, each stored with the actual ICE contract it represents.
- **Permanent history** — the session-volume history file is append-only forever, never cleaned.
- **Loud failures** — on any real error, fail non-zero with the offending **absolute file path**.
- **Zero collision** — the new feature modifies no existing file; reads source data read-only.

---

## 1b. Where everything lives (so this is never fuzzy)
Three separate places — do not conflate:
1. **Docs / control** — `Desktop\VLM_Session_Volume_Project\` (this folder). Planning only. No code runs
   here; the API never reads it.
2. **App code + data** — `Desktop\Options_flow_analyzer\`. Where `price_tape.py` runs and the RTD feed
   + tapes live. Gets ONLY the tiny sidecar write (`data/<date>/ct_futures_volume.csv`).
3. **Engine (NEW)** — `Desktop\vlm_session_volume\` (separate repo). All the bulk: compute engine,
   symbology, PERMANENT history, reports. Reads #2's `data/` **read-only**; writes only into itself.
4. **VLM API** — `vlmapi.vlmdata.com` (separate online service). Reads from **GitHub** (`vlmsofts` repos),
   NOT the Desktop. Later phase: publish the engine's history to GitHub, then add routes.

Decision: engine in a **separate repo**, tiny capture stub in the analyzer — keeps the analyzer small
and collision near-zero.

---

## 2. Methods / scripts

### 2a. New session-volume feature (to be built — separate repo)
| Method / file | Role | Status |
|---|---|---|
| `session_volume.py` | Core: extract both windows, per-contract call/put, RVOL tiers (5/10/20/30/60), report. CLI `--window {overnight\|day\|both} --commodity --date --no-write --backfill`. | PLANNED |
| `config.py` | Self-contained config: window times, lookback tiers, closed-dates, exclusion set, source path. Imports nothing from existing repo. | PLANNED |
| generic resolver | Extend December-only logic to H/K/N/Z at positions 1 & 2 (new repo; do not edit `contract_calendar.py`). | PLANNED |
| `api.py` | VLM Data Gateway routes under `/v1/flow/CT/session-volume/*`. | PLANNED (last phase) |
| `tests/test_session_volume.py` | Hermetic tests: window math, day-window subtraction, RVOL degradation, holiday guard, idempotent+permanent history, loud-fail-with-path. | PLANNED |

### 2b. Existing Options Flow Analyzer — methods this project reads or depends on (read-only)
Repo root: `C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\Options_flow_analyzer`

| File | Role |
|---|---|
| `overnight_volume.py` | First, narrow overnight-only version (RTD tape → overnight vol + single-avg RVOL). Superseded by `session_volume.py` after verification. |
| `options_tape.py` | Intraday options tape recorder. Writes `ct_options_tape.csv`; holds `.options_tape_ct.lock`; 10-day rolling cleanup of daily tapes. **Source of volume data.** |
| `price_tape.py` | Intraday futures price tape recorder → `ct_price_tape.csv` (no volume). |
| `run.py` | Single-day flow pipeline entry (`run.py --date --commodity CT`). Writes `processed/`. Not used by this feature. |
| `pipeline/contract_calendar.py` | **Symbology engine** — `actual_contract`, `parse_generic`, `generic_to_actual`, `actual_to_generic`, roll detection. Reuse for generic↔ICE↔year mapping. |
| `pipeline/flow_aggregator.py` | Weekly flow aggregator; has its own `--lookback` (NOT reused — different source/unit). |
| `pipeline/b76.py` | Black-76 math (price/greeks/IV). |
| `pipeline/iv_snapshot.py` | EOD IV surface capture. |
| `pipeline/gateway_settle.py` | GEX settle from VLM gateway. |
| `gex_calculator.py` / `gex_surfaces.py` / `gex_chart_data.py` / `gex_settle_run.py` / `gex_backtest*.py` | GEX gamma-exposure engine + backtest. |
| `settle_watcher.py` / `settle_watcher_kc.py` | ICE settlement detectors (CT, KC). |
| `flow_watcher.py` / `outlook_watcher.py` | Daily flow-email monitor / pipeline trigger. |
| `alerts.py` | Gmail SMTP alert sender. |
| `vlm_gateway.py` | Thin read-only client for the VLM Data Gateway. |

---

## 3. Paths

### 3a. This project
| What | Path |
|---|---|
| Project docs home (this folder) | `C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\VLM_Session_Volume_Project` |
| New feature repo (to create) | `C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\vlm_session_volume` *(sibling — name TBD by analyzer)* |
| Permanent history (in new repo) | `…/vlm_session_volume/data/history/session_volume_history.csv` (append-only forever) |
| Per-session outputs (in new repo) | `…/vlm_session_volume/data/<date>/session_volume.{txt,json}` |

### 3b. Existing app (read-only sources for this project)
| What | Path |
|---|---|
| App repo root | `C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\Options_flow_analyzer` |
| Options tape (volume source) | `…/Options_flow_analyzer/data/<YYYY-MM-DD>/ct_options_tape.csv` |
| Price tape | `…/data/<YYYY-MM-DD>/ct_price_tape.csv` |
| RTD snapshot | `…/data/<YYYY-MM-DD>/rtd_snap.json` |
| History dir | `…/data/history/` |
| Config | `…/config/settings.py` (EXPIRY_DATES, `CT_EXCLUDED_MONTH_PREFIXES={'CTV','CTQ'}`, TAPE times) |
| Tape lock | `…/.options_tape_ct.lock` (do not touch) |
| Decision log | `…/MEMORY.md` (incl. IFUS 2026 CT holiday entry) |
| IFUS calendar source | `…/reference/IFUS_Trading_Hours_Holiday_Calendar_2026.pdf` (copy in this folder) |

### 3c. Schedules & misc
| What | Path |
|---|---|
| Scheduled tasks store | `C:\Users\Louis\Claude\Scheduled\<taskId>\SKILL.md` |
| Daily pipeline launcher | `…/Options_flow_analyzer/Run_Daily_Pipeline.bat` |

---

## 4. Scheduled tasks
| Task ID | Schedule | Runs | Status / note |
|---|---|---|---|
| VLM Session Volume Morning | Weekdays 07:15 ET | `session_volume.py --window overnight` | LIVE (3b). Verified vs old overnight history (546 exact match). |
| VLM Session Volume EOD | Weekdays 14:35 ET | `session_volume.py --window both` | LIVE (3b). Writes full day window + history row. |
| `overnight-options-volume` (old) | Weekdays ~07:24 ET | `overnight_volume.py` | LEGACY — retire after 2–3 parallel sessions confirm the new jobs. |

Note: scheduled tasks only run while the Claude desktop app is open; a missed run fires on next launch.

---

## 5. Data sources

### 5a. Local
| Source | What it provides | Notes |
|---|---|---|
| RTD options tape | Per-strike cumulative + delta call/put volume, OI, settle, ~20s polls 21:00→15:30 ET | The volume series; tapes self-delete after 10 days. |
| RTD price tape | Futures last/bid/offer/mid/settle | No volume. |
| IFUS 2026 holiday calendar | CT trading-day closures | Closed: Jan 1, Jan 19, Feb 16, Apr 3, May 25, **Jun 19**, Jul 3, Sep 7, Nov 26, Dec 25, Jan 1 2027. Beware shortened sessions (early closes) not in the PDF. |

### 5b. VLM Data Gateway  (`https://vlmapi.vlmdata.com`, header `X-VLM-API-Key`, GET, v1)
Standard envelope adds `cached` / `stale` / `stale_age_seconds`. Reads GitHub source files via
`github_reader`, 300s cache, stale fallback. Full reference: `VLM_API_REFERENCE.md` (this folder).
| Endpoint (relevant) | Provides |
|---|---|
| `/v1/openinterest/CT` | CT futures OI, 16 cols incl. `bbg_ticker` (CTDEC1 Comdty…), generics back to 2008. |
| `/v1/openinterest/CT/options` | CT options OI + Black-76 Greeks (latest). |
| `/v1/openinterest/options/history` | Options OI history with Greeks (filter date/month/strike/pc). |
| `/v1/spreads/CT` , `/CT/history` | CT calendar-spread OHLC (Oct excluded from Bloomberg CT spread chain). |
| `/v1/flow/CT/session-volume/*` | **PLANNED** — this project's endpoints (latest / {date} / history). |
| `/v1/gex/CT/*`, `/v1/flow/CT/parsed` | PLANNED (design spec in reference). |

### 5c. Bloomberg contract symbology (key for long-timeline comparison)
- **Sequence generics** `CT1, CT2` = front / 2nd by curve position (roll by position).
- **Calendar-locked generics** `CTDEC1, CTDEC2…` = fixed month, N years forward. As of Jun 2026:
  `CTDEC1`=CTZ6 (Dec 26), `CTDEC2`=CTZ7 (Dec 27). Generics never die — continuous "slot" history.
- ICE month letters: F Jan, G Feb, H Mar, J Apr, K May, M Jun, N Jul, Q Aug, U Sep, V Oct, X Nov, Z Dec.
- **Canonical stored key:** `ice_code` (CTZ6) + `generic_code` (CTDEC1) + 4-digit `delivery_year`
  + `month_code` + `month_name`. (Single-digit ICE year recycles each decade — 4-digit year required.)
- Joins to VLM OI/settle history on `generic_code`.

---

## 6. Companion documents in this folder
| File | Contents |
|---|---|
| `PROJECT_REGISTRY.md` | This index — methods, paths, sources, schedules. Keep current. |
| `PHASE3_AUTHORIZATION_session_volume.md` | Locked spec / build authorization for the analyzer (investigate→plan→build gates). |
| `PHASE3C_API_session_volume.md` | Phase 3c spec — VLM Data Gateway `/v1/flow/CT/session-volume/*` routes, schemas, source files, gated build, open questions. |
| `INVESTIGATE_hardrules_additive.md` | Investigate-only prompt: collision + feasibility for Oct/Aug exclusion, 8-slot universe, symbology + per-contract history, via additive columns/files (zero corruption). |
| `PLAN_collision_blastradius_proof.md` | Evidence-backed proof that no other consumer's collection changes + open decisions (D1–D4) + empirical sign-off gate. No code until all boxes ✓. |
| `FUTURES_VOLUME_feasibility.md` | Pivot to futures-contract volume (D1) + drop serials (D2). Futures vol is in the RTD feed but discarded by price_tape.py; append a `volume` column (additive, verified invisible to gex). Full-session/daily available back to 2008 via API; overnight/day split forward-only. Open decisions + proof gate. |
| `FUTURES_locked_scope_and_pricetape_investigate.md` | LOCKED futures scope: 3 windows (night/day/full) + 4 over-time comparisons (incl. night-vs-day share/ratio). Forward-only split accepted. Investigate-only prompt for the price-tape `volume` column: map ALL price-tape-family readers (daily/history/backup), full pytest + gex-unchanged proof, cadence decision. Sign-off gate. |
| `BUILD_futures_session_volume.md` | **THE single build doc** to hand the analyzer. Two-piece architecture (tiny sidecar capture in analyzer + engine in new vlm_session_volume repo), full spec, gated stop-points, proof gate, hard rules. |
| `cotton_futures_volume_history_blpapi.py` | Bloomberg seed/backfill puller — one `HistoricalDataRequest` for the 8 generics' daily volume/OI/price history (deep, years back). Run on the Terminal machine → clean long-format CSV → seeds the engine's permanent full-session history. Adapted from the owner's `cotton_options_blpapi.py` template. |
| `PROMPT_FOR_ANALYZER_session_volume.md` | Original investigate-and-plan-only handoff prompt. |
| `VLM_API_REFERENCE.md` | Full VLM Data Gateway API reference. |
| `IFUS_Trading_Hours_Holiday_Calendar_2026.pdf` | Authoritative ICE 2026 trading-holiday source. |

---

## 7. Changelog
| Date | Change |
|---|---|
| 2026-06-17 | Registry created. Documented existing app methods/paths/sources, the session-volume plan, hard rules (Oct/Aug excluded, 8-slot capture universe, permanent history, loud-fail), symbology, VLM API targets, and scheduled tasks. |
| 2026-06-17 | Phase 3a/3b verified live (overnight 546 == old history; Morning 07:15 + EOD 14:35 jobs ready). Issued `PHASE3C_API_session_volume.md`: gex-aligned `/v1/flow/CT/session-volume/{daily,latest,history}` routes. Elevated the per-contract permanent history (`session_volume_by_contract.csv`) from optional to REQUIRED for December-across-years. Flagged that the gateway is a separate production codebase needing the same zero-collision discipline. |
| 2026-06-17 | `full` window (21:00→14:20 = overnight+day) confirmed already built & correct. Read review found 3 gaps vs locked spec: Oct/Aug NOT excluded, no 8-slot universe/symbology/per-contract history, and the script lives in-repo importing `config/settings` (not the separate repo). Issued `INVESTIGATE_hardrules_additive.md` (investigate-only): additive-columns approach (`*_universe` cols + new per-contract file) so nothing already captured changes meaning. Repo-placement decision deferred until that report lands. Consumption to be via VLM API endpoints. |
| 2026-06-17 | Analyzer investigation reviewed; impact numbers independently re-verified off the tape (06-12=9,997; 06-16=4,710 — exact match). Correction: serials carry ~0 volume so folding is a no-op today. Did a repo-wide sweep proving NO other consumer's collection changes (no glob hits our files; cleanup deletes only the tape file; settings additions read only by us; tape read-only). Issued `PLAN_collision_blastradius_proof.md` with open decisions D1–D4 and an empirical sign-off gate (full pytest + dry-run diff). NO code until all boxes ✓. |
| 2026-06-17 | DECISION SHIFT: D1 = futures-contract volume (not options); D2 = drop serials. Found futures volume is in the RTD feed every poll but `price_tape.py` discards it. Verified the additive-column path is safe: append `volume` to the price tape — `gex_calculator` reads by name (pandas) so it's invisible; `test_price_tape` checks header==_FIELDS + row counts (auto-updates). Full-session/daily futures vol available historically (rtd_snap + VLM API `oi_data` to 2008); overnight/day split is forward-only. Issued `FUTURES_VOLUME_feasibility.md`. Existing options `session_volume.py` fate TBD. Still NO code. |
| 2026-06-17 | SCOPE LOCKED. Owner confirmed: futures volume, 3 windows (night/day/full) + 4 over-time comparisons incl. night-vs-day share/ratio; forward-only split accepted (starts today); full-day history from API to 2008. Options view: stand down jobs, keep engine to repurpose. Issued `FUTURES_locked_scope_and_pricetape_investigate.md` — investigate-only on the price-tape `volume` column (map all price-tape-family readers incl. ct_price_tape_history.csv, full pytest + gex-unchanged proof, cadence choice). No code until the Part 3 gate is all ✓. |
| 2026-06-17 | Part 2 investigation returned & independently re-verified (analyzer + 2 owner-spawned agents + my re-grep all agree): 5 readers, all by-name; tests auto-update off live `_FIELDS`; column-append = zero collision. Caught a gap: those proofs cover added COLUMNS, not the added ROWS that boundary-flush (option c) would create (gex reads rows). DESIGN LOCKED → **sidecar file** `ct_futures_volume.csv` (written by price_tape.py, boundary-flushed 07:00/14:20). Price tape rows+columns untouched → verification fully covers it, no rows-proof outstanding. volume+oi both captured. Options jobs: stand down after 1 parallel futures session. AWAITING owner verbal all-systems-go before code. |
| 2026-06-17 | STRUCTURE LOCKED (bloat concern): engine goes in a **separate `vlm_session_volume` repo**; analyzer gets ONLY the ~10-line sidecar write. Lowest bloat + lowest collision. Wrote the single consolidated **`BUILD_futures_session_volume.md`** (two-piece architecture, gated stop-points, Part A proof gate, hard rules). Added a "where everything lives" map to the registry. Build doc ready to hand over on the owner's verbal all-systems-go. |
| 2026-06-17 | FINAL PRE-CODE VERIFICATION PASSED (analyzer, read-only, corroborated): cleanup is named-file-only (never touches `*_futures_volume.csv`); `volume`+`oi` confirmed live per-contract in the RTD feed (`ice_rtd_reader.py:334/343`); sidecar lands in `data/<date>/`; `_FIELDS` untouched. Timezone resolved: boundary-flush uses naive `datetime.now()` matching the loop (machine = ET, owner-confirmed) + once-per-boundary latch — recorded in the build doc. All Part A premises source-verified. Analyzer holding at pre-code line; awaiting owner verbal all-systems-go. |
| 2026-06-17 | CORRECTNESS FIX (owner-caught): rejected exact-minute-only boundary flush — on a disconnect across the boundary minute it would create a SILENT, permanent, non-replayable hole (violates loud-fail). Build doc updated: fire on first poll **on/after** boundary + record `timestamp`/`staleness_seconds`, bracket with the last pre-boundary poll, and if nearest reading is beyond tolerance (5 min) set `reliable=false` and LOUD-FLAG the window. Normal 20s drops invisible; real outages surfaced + dated. |
| 2026-06-17 | RIGHT-SIZED (owner pushback on overengineering): volume is the **cumulative** session counter, so 20s granularity is irrelevant and night/day/full are just boundary-counter differences. Dropped the `staleness`/`reliable` columns + loud-flag as over-spec. FINAL boundary rule = fire on **first poll on/after** boundary (one-line change from exact-minute), 7-field schema unchanged, 3 rows/contract/day, nothing 20s-granular stored. Part A send-back is just this one-line amendment; everything else in Part A already passed. |
| 2026-06-17 | **Auto-start task registered + verified:** `VLM_CT_PriceTape_Start` launches `price_tape.py --commodity CT` at **20:55 ET Sun–Thu** (DaysOfWeek=31), `ExecutionTimeLimit=PT0S` (no limit), `MultipleInstances=IgnoreNew`, `StartWhenAvailable=True`. Removes the manual launch. (VBA-on-the-RTD-workbook path was rejected: saving `ICE RTD FEED CT.xlsx`→`.xlsm` renames a workbook hardcoded in `settings.RTD_WORKBOOKS`, would break the whole RTD read.) Operational dependency: RTD workbook must be open during the session for live capture. First live capture = tonight's launch → 06-18 session. NEXT: owner pings 06-18 after 14:20 → verify sidecar (open/07:00/14:20) + end-to-end engine demo → register single daily engine job (~14:40). THEN next phase = **UI/front end** (night/day/full + RVOL + December-over-years, PNG export, search/query) on top of the permanent history + the Phase 3c API endpoints. |
| 2026-06-17 | **Engine rewritten RTD-direct (83 tests green).** Reads sidecar directly for night/day/full; Bloomberg seed = full-only historical fallback; RVOL always from seed. `--window overnight|eod`. Net -1 test (5 old share/split tests → 4 new direct tests). Confirmed on disk: NO sidecar `ct_futures_volume.csv` exists yet for 06-16/06-17 — Part A sidecar not running live; today's history row is bbg_seed preliminary (68,126), not RTD. BLOCKER (operational, on owner): run patched `price_tape.py` live through one full session (21:00→07:00→14:20, next is 06-18) to produce a real sidecar. Then end-to-end demo + verify before registering the single `VLM_CT_FutVol_EOD` (~14:40 ET) job. (06-19 Juneteenth = CT closed.) |
| 2026-06-17 | **ARCHITECTURE RE-AFFIRMED (owner caught a drift):** Bloomberg = ONE-TIME reseed (deep history, done); **ICE RTD = the daily driver** (sidecar gives night/day/full directly). I had wrongly written "daily forward pull from Bloomberg" into the build doc after the ICE check — corrected. Daily Bloomberg `VLM_CT_FutVol_SeedRefresh` task was registered then **DISABLED** (kept refresh_seed.py/blpapi puller as manual reseed tools). Overnight/EOD tasks never registered. Removed the "split=share×Bloomberg full" mechanism. NEXT (re-alignment send-back issued): engine must source today's full+night+day from the RTD sidecar (seed = historical baseline only); single daily job ~14:40 ET reading today's sidecar; confirm price_tape sidecar runs live; show one real session before scheduling. |
| 2026-06-17 | **JOBS REVIEW (4→3 proposed; gate caught 2 are no-ops).** Analyzer fixed prior issues: tasks repoint to `futures_session_volume.py`, added `refresh_seed.py` (puller `--merge` trailing 15d → engine `--window final --date yesterday`); path-consistency VERIFIED (refresh writes the same `VLM_Session_Volume_Project\cotton_futures_volume_history.csv` that engine's `FUT_SEED_CSV` reads). BUT traced the engine: `full_for_date`=seed-only, and `main()` defaults overnight(07:15)+eod(14:35) to TODAY — never in seed at those times → both warn & write nothing. Same-day full is impossible anyway (Bloomberg settled = T+1). RECOMMENDATION: register ONLY `VLM_CT_FutVol_SeedRefresh` (09:00, finalizes yesterday w/ full + sidecar split); DROP/HOLD Overnight+EOD (no-ops; could only ever show shares). Registration needs elevated PowerShell (admin). |
| 2026-06-17 | **SEED VERIFIED in `vlm_session_volume/data/history/`** (5,398 sessions 2005→06-17, source=bbg_seed; by-contract 33,212 rows, symbology clean, latest sums to 68,126). Engine landed in its own repo `Desktop/vlm_session_volume` (separate, as designed). **JOBS NOT READY — gate caught 2 problems in the drafted task XMLs:** (1) both tasks invoke `session_volume.py` = the OPTIONS engine (ct_options_tape), NOT the verified `futures_session_volume.py` — leftover args from the retired options jobs; (2) NOTHING refreshes the Bloomberg seed — engine reads `cotton_futures_volume_history.csv` but no job re-runs the puller, so forward runs find no data for new dates, and 06-17's seeded value is the preliminary 13:57 mid-session pull (final posts ~T+1). DO NOT register. Send-back: repoint to futures engine; add a daily seed-refresh job with trailing ~10-15d re-pull (preliminary→final self-heals); same-day 14:35 run = preliminary; re-show 3 task defs. (Note: VLM gateway codebase is local at Desktop/vlm-data-gateway — for Phase 3c.) |
| 2026-06-17 | **PART B BACKTEST VERIFIED (independent reproduction).** Re-computed from the seed CSV: FULL 68,126, prev 56,324, and ALL 5 RVOL tiers (5/10/20/30/60 = 77,524/82,894/77,916/78,764/80,522; 0.88/0.82/0.87/0.86/0.85x) match the engine to the dollar. Generic→actual+delivery_year mapping correct; night+day==full holds; Dec-over-years real & decade-safe (CTDEC1 mid-June 2016=22,728 … 2026=34,950). Oct/Aug excluded, 8-slot clean, pytest 84 green, zero analyzer files touched beyond the sidecar. Recommended **(a) write the seed** — verified, idempotent/append-only, own-file-only. Open item (21:00 reset) doesn't block seed (forward-split only). Next: seed → Part A sidecar live → jobs (own stop) → API. |
| 2026-06-17 | **VERIFICATION PASSED vs ICE OFFICIAL.** Owner provided the ICE Futures Daily Market Report (CT, 12-Jun-2026). Per-month TOTAL VOLUME vs the Bloomberg pull: thin back months EXACT (May-27 3,821 vs 3,820; Dec-27 790; Mar-28 38; May-28 0), active fronts within ~2-4% (Dec-26 36,084 vs 34,950; Jul-26 27,095 vs 26,503). 8-slot sum ICE 82,260 vs bbg 79,967 (2.8%) — residual = ICE includes TAS/TIC/strategy volume per its own note; Bloomberg PX_VOLUME is outright. CONCLUSION: Bloomberg pull is CORRECT & authoritative; oi_data (29,679) was the low-vintage outlier (ICE Dec-26 alone > oi_data 8-sum). **CLEARED TO SEED** from the Bloomberg pull. Standardize on Bloomberg PX_VOLUME for the whole series; sidecar = night/day split only, as a share of the authoritative full. |
| 2026-06-17 | **Bloomberg pull ran:** 43,752 rows, 8 generics, 2005-01-03→2026-06-17 (5,482 days); fronts ~full coverage, 2nd generics sparser (expected). **VERIFICATION CAUGHT A DISCREPANCY:** Bloomberg 8-generic daily sums are ~2.7x the oi_data 8-slot totals (06-12: bbg 79,967 vs oi_data 29,679); per-generic shows CTDEC1 alone (34,950) > entire oi_data 8-sum — a systematic level diff, NOT missing-generics/double-count. Diagnosis: VINTAGE — HistoricalDataRequest = final cleared volume; oi_data = lower 09:35-EST morning snapshot. Bloomberg matches the owner's spreadsheet magnitude → likely authoritative; oi_data is the outlier. NOT SEEDED yet — awaiting owner spot-check vs his sheet (CTDEC1 06-12 ≈ 34,950?). DESIGN FLAG: full-session must come from ONE source (Bloomberg-final for history+forward); sidecar provides only the night/day SPLIT as a share of the authoritative full, else night+day won't reconcile with full at the history→forward seam. |
| 2026-06-17 | Captured two owner reminders: (1) generic data parsing — HistoricalDataRequest returns one securityData per generic with a per-date fieldData array; puller flattens to long table; engine attaches actual-contract + delivery_year per date via symbology (generic is continuous front-month; volume has no roll-gap). (2) NEW API endpoint(s) for all the new data, for the whole API family — added `/v1/futures/{commodity}/volume-history` (deep generic series) to PHASE3C alongside the session-volume routes. Both later-phase, additive. |
| 2026-06-17 | **DEEP HISTORY RESTORED via Bloomberg.** Owner's `New Futures History.xlsx` (Excel-Claude export) confirmed Bloomberg carries daily VOLUME for the exact 8 generics back ~13+ yrs — so the multi-year December view + deep RVOL tiers ARE achievable (just not from oi_data, whose volume is ~2wks old). Plan: seed the engine's permanent full-session history from a one-time Bloomberg `HistoricalDataRequest`. Wrote `cotton_futures_volume_history_blpapi.py` (adapted from owner's `cotton_options_blpapi.py`; ReferenceData→HistoricalData, 8 generics, PX_VOLUME+OI+price+OHLC+EFP/EFS, daily, active-days-only). Runs on the Terminal machine (sandbox has no Terminal). Night/day split still forward-only. Note: I can't talk to Claude-in-Excel live — bridge is file export, which works. |
| 2026-06-17 | **DATA CORRECTION (Part B backtest exposed it):** `oi_data.csv` `volume` column only exists from ~2026-05-12 (API-ref field note "empty before 2026-05-12"); live-verified = 11 nonzero CT 8-slot sessions (2026-06-03→06-17). Only OI reaches 2008, NOT volume — the BUILD doc's "volume back to 2008" was an error (mine). So full-session volume RVOL has NO deep history to seed; it accrues forward like night/day. Engine RVOL math itself verified correct (RVOL-5/60 reproduce from raw oi_data) — the bug was averaging zeros into deep tiers. DECISION Q1: **degrade gracefully** (count only real nonzero sessions; `n/a (have M of N)`; no zeros, no OI-proxy). Build doc corrected. Q2 (backtest scope) pending. |
| 2026-06-17 | **PART A COMPLETE & VERIFIED.** Amendment built correctly: `_boundary_datetimes()` uses absolute datetimes with `open` offset −1 day (prior evening 21:00), compared via `now >= dt` — wrap trap handled (verified in code). `_FIELDS` byte-identical; sidecar 7 fields; additive only (265 ins / 0 del); pytest 498 green; outage + no-misfire tests added. `open` correctly fires on first poll after 21:00 launch (test assertion fixed, not code). Gate passed — cleared to start Part B (new-repo engine), which has its own backtest stop-point. |
| 2026-06-18 | **CONSOLIDATED to single folder + CRITICAL integration fix.** Per owner: entire project now runs out of `VLM_Session_Volume_Project` (engine `vlm_session_volume` merged in; old folder emptied). Repointed the two task XMLs to `futures_session_volume.py` (they were still calling the OPTIONS `session_volume.py` — that produced the 166 options number at 07:15). Added missing holiday **2026-06-19 (Juneteenth)** to `config.CT_CLOSED_DATES`. **Root-caused why every history row was `bbg_seed`:** `read_sidecar_direct` keyed the universe by generic code (CTDEC1) but the live `price_tape.py` sidecar writes ICE codes (CTZ6) — so all contracts were dropped and no real RTD row ever wrote. Hermetic tests passed only because their fixtures used generic codes. FIX: `read_sidecar_direct` now normalises ICE→generic via `contract_resolver.ice_to_generic` (handles both formats; Oct/Aug still excluded). Added ICE-coded regression test. Proven end-to-end on today's real sidecar (night=13,428 over the 8-slot universe; RVOL from seed; `source=rtd` row + by-contract symbology written). pytest 84 green. REMAINING: register jobs (elevated PS); first real row writes at the 14:35 EOD run after the 14:20 boundary lands. |
| 2026-06-18 | **Capture-completeness monitor added** (`check_capture.py`). Verifies the sidecar has every DUE boundary (open/0700/1420) for the trading date, holiday/weekend-aware (config.CT_CLOSED_DATES incl. Juneteenth); loud-fail exit 2 with project + absolute sidecar path; optional alerts via Twilio WhatsApp/SMS (env TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM/TO) or SMTP email (env SMTP_*). Tested: missing-boundary FAIL, complete OK, holiday OK. Schedule `VLM_Session_Volume_CaptureCheck.xml` (14:40 ET weekdays, `--eod`, runs 5 min after the EOD engine). Closes the silent "no data" gap. Also caught/removed 3 stale OPTIONS Task Scheduler jobs (session-volume-eod, "VLM Session Volume EOD/Morning"); registered futures `VLM_Session_Volume_EOD`. Open: reconcile duplicate price-tape launchers (CottonPriceTape vs VLM_CT_PriceTape_Start). |
