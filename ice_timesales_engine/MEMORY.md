# MEMORY.md — ice_timesales_engine

> Read at session start. Log every significant decision: What / Why / What was rejected.
> Created 2026-07-04 (Session 1 — full one-shot build).

---

## Project purpose

Daily-collected analytics engine over ICE futures **time & sales tick tape**
(the EOD blotter files in `C:\Ice eod records\`), slicing traded volume by
arbitrary time block × contract month × aggregate × trade type
(outright / leg / EFS / EFP / block), served through a low-latency query API
and Flask/Plotly UI. CT first; softs (KC/CC/SB) parametrized.

Build plan: `ICE_TIMESALES_ENGINE_BUILD_PLAN.md` (authoritative spec, on
Lou's desktop / chat handoff). All ground-truth facts in it were verified
against live files 2026-07-04.

---

## HARD RULES

1. **`C:\Ice eod records\` is READ-ONLY.** The engine never writes, moves,
   renames, or "cleans" anything under that tree. All derived artifacts go to
   the DB, this repo's `data/`, and `logs/`.
2. **Settle files are cross-check only, NOT ground truth** — they carry stale
   carry-forward rows (verified: the 07-02 settle file repeats 07-01's volumes
   wholesale). The tape is self-validating.
3. **Missing blotter file = zero volume by design** (capture skip-probes
   zero-volume months), never an error.
4. **Rollup writes SIDECAR CSVs only** (`*_ICE.csv` in this repo). Writing into
   the shared `VLM_Session_Volume_Project/data/history/*` files requires Lou's
   explicit approval (blast-radius gate).
5. **Symbology and session windows are IMPORTED** from
   `VLM_Session_Volume_Project` (`contract_resolver`, windows, holiday set) —
   never re-hardcoded.

---

## Decisions log

| # | Decision | Why | Rejected |
|---|---|---|---|
| 1 | `full_total` = raw all-in tape sum (incl. EFS/EFS-Delete/EFP) | Reproduces the locked acceptance numbers; self-validating; by-condition sum == night+day == full | Defining the stored total as "clean" (excl. deletes) — that's a UI toggle, not the canonical number |
| 2 | One mutually-exclusive `primary_type` per print via precedence ladder (efs_delete > efp > efs > block > leg > outright) | Buckets must sum to the total with zero double-count; multi-tag membership (`BlockTrde, Leg`) drives filter views only | Counting a multi-tag print in several summed buckets |
| 3 | Dual-dialect Db shim (Postgres via psycopg / SQLite local) with portable SQL; minute truncation + bucket folding in Python | One codebase runs Supabase in prod and file-DB in dev/tests; no live DB needed to prove correctness | Postgres-only (untestable offline), SQLite-only (no hosted UI) |
| 4 | Naive-ET timestamp storage as ISO TEXT | Windows are defined in ET wall-clock; the tape is ET wall-clock; lexicographic order == chronological; DST-safe for this use | timestamptz (adds conversion risk for zero benefit here) |
| 5 | Hot path = 1-minute pre-aggregated buckets (`minute_agg`); sub-minute windows fall back to tick scan | Arbitrary windows sum ≤1,440 rows/day instead of tens of thousands of ticks; measured median 3.3ms | Scanning raw ticks per query |
| 6 | Idempotency = PK `(commodity, session_date, ice_code, seq_num)` + whole-file sha256 skip in `ingest_log` | Re-runs insert 0 rows (verified); file-rewrite detection | Trusting filenames/mtimes |
| 7 | Reconcile SUSPECT_GAP_PCT = 0.50, labels surface-only | 07-02's known short-capture gap was 32%; 50% leaves headroom for heavy-spread days; never blocks ingest | Enforcing reconciliation (settle side is itself unreliable) |
| 8 | Block volume tracked in `block_supplement` (sources 'tape' + 'spreads'), never added to tape totals | Avoids double-count; the rare `BlockTrde, Leg` print is already in the tape total via primary_type=block | Adding spreads Block Volume into session totals |
| 9 | Cloudflare purge by cache-tag `sv:{cmd}:{date}` after ingest; no-op when unconfigured | Daily collector is sole writer → clean event-driven invalidation; long TTL for closed sessions | TTL-only (stale latest-day risk), purge-everything |
| 10 | Softs month-sets in `commodity_meta.py` (SB has NO Dec) | UI picker/aggregation must be commodity-correct; verified from `ice_eod_capture_softs.py` | Hardcoding CT's Mar/May/Jul/Dec |
| 11 | Blank-condition prints fold into `outright` (2026-07-04, Lou) | A blank print is a real outright fill with no aggressor stamp (verified 07-02: 454 prints, real prices 76.88–77.85) — not a separate category. ask/bid/unstamped kept as `outright_side` sub-split, informational only | A standalone `blank`/`other` bucket — meaningless to a trader |
| 12 | Reconcile-vs-settle table REMOVED from the trader UI (2026-07-04, Lou) | Settle files are stale/untrusted; the tape is clean and self-validating, so delta/label had no trading purpose. Kept server-side at `/{cmd}/reconcile` for ops only | Keeping/relabeling it on the board |

---

## Verified acceptance (2026-07-04, real data end-to-end)

- **46/46 tests green**, incl. `test_acceptance_0702.py` off the byte-copy fixture.
- Backfill of **07-01 + 07-02** (Lou: the two complete, canonical-format days):
  44,871 ticks, 10 files, re-run inserts 0 (sha256 skip).
- 07-02 CT Z26 via API: night **3,667** / day **14,699** / full **18,366**;
  outright-only 13,247 (incl. 522 blank/unstamped); CTDEC1 generic filter → 18,366; 60m profile buckets
  match the hand-computed hourly table exactly (21:00→520, 22:00→157, …).
- 07-01 reconciles EXACT (tape 27,003 = settle 27,003, delta 0) — the
  complete-day proof. 07-02 settle exposed as stale carry-forward by the flags.
- Latency (local SQLite, no edge): min 3.0 / median 3.3 / p90 3.8 ms.
- Resolver nuance verified: N7 = CTJUL2 on 07-01 but CTJUL1 on 07-02 (Jul-26's
  delivery month began 07-01 → generic board rolled). Calendar-locked, correct.

---

## Deployment state

- **Local: fully working** (SQLite `data/ice_timesales.db`, Flask :5061).
- **NOT yet deployed** to Supabase/Railway/Cloudflare — needs Lou's env vars
  (`DATABASE_URL`, `CF_ZONE_ID`, `CF_API_TOKEN`) and a deploy decision.
- **NOT yet scheduled** — daily job command:
  `python -m jobs.daily_ingest --commodity CT` (run after the EOD capture,
  ~16:15 ET; holiday/no-blotter days exit clean).

## Next session priorities

1. **Monday 2026-07-06: full 45-day backfill** (Lou re-runs the capture with
   `--date` for missing days; servers back up) then
   `python -m jobs.backfill --commodity CT`.
2. Supabase: set `DATABASE_URL`, run backfill against it; Railway deploy;
   Cloudflare in front (tokens).
3. KC/CC/SB: parametric resolver extension (contract_resolver is CT-only;
   `to_generic` returns None for softs today — tape still ingests fine).
4. Decide with Lou whether the ICE rollup ever merges into the shared
   VLM_Session_Volume_Project history files (blast-radius gate).

## 2026-07-05 — bar5m archive + Bloomberg 6.4-month seed (built, verified, live)

**What:** permanent 5-minute archive table `bar5m`, source-labeled
(`ice`|`bloomberg`), never mixed. Lou rulings encoded: 5-min is the minimum
grain he will ever query; Supabase step is Lou-executed only
(SUPABASE_RUNBOOK_LOU.md — nothing cloud-touching runs automatically).
- `ingest/bbg_map.py`: Bloomberg conditionCodes → primary_type (verified to
  the exact lot vs the ICE tape on 07-01/07-02; residual 'I' → 'other',
  never absorbed; `includeNonPlottableEvents=True` is MANDATORY or Bloomberg
  hides all leg/EFS/EFP/block prints).
- `ingest/bar5m.py`: rollup_ice_bar5m (from minute_agg, delete+reinsert per
  day) + replace_bloomberg_day. daily_ingest now emits bar5m after minute_agg.
- `jobs/seed_bloomberg.py`: one-shot seed, DATED tickers (generics stitch by
  today's mapping — unusable across rolls), UTC→ET, session-date = ET>=21:00
  rolls to next trading day, 21-day chunks, strictly sequential requests.

**Seeded + verified:** 1,211 contract-days, 10,162,374 lots, 10 CT contracts,
2025-12-22 → 2026-07-02 (Bloomberg's measured tick-retention wall), zero
holiday rows, DB 51.7 MB. Acceptance: CTZ6 07-02 night 3,667 / day 14,699 ==
locked ICE numbers; every trade-type bucket exact on both smoke days.
Split: outright 5.70M / leg 4.00M / efs 295k / block 61k / efp 59k /
efs_delete 8k / other 45k (0.45%).

**Why:** Bloomberg only retains ~6.4mo of intraday ticks; ICE files are
forward-only. Seed once, grow forward with ICE (which carries the trade-type
tags natively). **Rejected:** generic tickers for the seed (roll ambiguity);
seeding into `ticks` (no seq_num, would pollute the tape tables); R2/parquet
tiering (5-min grain makes the whole multi-year archive <1GB — unnecessary).

## 2026-07-14 — session log: Supabase automation live + multi-day dashboard + source rule C

**Supersedes the "Deployment state" section above** (NOT-deployed / Lou-executed-only
are both stale): Supabase is populated, verified, and written to AUTOMATICALLY daily.

**Completed (07-06→07-07):**
- Supabase bulk copy finished + independently verified exact (all 6 tables;
  ticks 130,490 / bar5m 195,188 / minute_agg 15,249 + fingerprints). Root cause
  of the 07-06 block: a STALE DB PASSWORD, not the Supabase platform incident —
  dashboard password reset fixed auth instantly (see ERRORS.md + memory
  `supabase-bulk-copy-lesson`).
- Daily automation: `run_daily_ingest_all.bat` (CT/KC/SB/CC loop, refuses to run
  without DATABASE_URL so it can't silently fall back to SQLite) + Task Scheduler
  task "VLM ICE Timesales Engine - Daily Ingest", daily 17:10 ET (after the three
  ICE capture tasks). DATABASE_URL = persistent User env var via setx (value never
  entered chat). Verified through the REAL dispatch path: Start-ScheduledTask →
  LastTaskResult 0, Supabase counts correct, sha256-skip idempotency held on re-run.
- Backfills: CC blotter backfill done (38 ok / 5 retention-edge fails 04-27→05-01,
  same pattern as CT/KC/SB); ingest backfills pushed KC/SB/CC into Supabase.
  Verified 07-14: all four commodities current through 2026-07-13, task green.

**Completed (07-14) — dashboard multi-day + A-vs-B + full history:**
- `/v1/sessionvol/{cmd}/profile` now accepts `from=&to=` → `per_date`/`windows`
  keys (single-`date` response shape UNCHANGED — additive only). Same window
  preset (full/night/day/custom) resolved per session date in every mode.
- Fixed a latent bug: custom `start`/`end` was silently overridden by the default
  `'full'` preset ("2am–4am over 20 days" returned full sessions). Now custom wins
  when no preset is given.
- Dashboard view modes: Single day · Continuous (stitched, overnight gaps
  compressed, Bloomberg-style categorical axis) · Overlay (time-of-day lines,
  newest gold) · **A vs B** (two arbitrary sessions, two independent single-date
  fetches, delta bars B−A + A|B|Δ tables; per-bucket deltas verified to sum
  exactly to the window-endpoint B−A) · Daily totals. renderTables restores
  the table headers A-vs-B rewrites.
- **Source rule C (Lou): one source per era, never mixed.** `bloomberg_cutoff()`
  in repository.py: dates ≤ max bloomberg session_date (CT: 2026-07-02) served
  from bar5m source='bloomberg'; later dates from live ICE tables. Why: 5 CT dates
  carry both sources; ICE's 2026-05-01 is ~53% partial (41,337 vs seed 88,045).
  Rejected: magnitude heuristic (breaks quietly), always-ice (serves the broken
  day), UI source picker (pushes the problem to the user). Seed grain 5-min →
  1-min requests flagged `bucket_minutes_effective: 5`. KC/SB/CC: cutoff=None,
  pure ice, untouched.
- Verified: catalog CT=139 dates (2025-12-22→2026-07-13); 05-01 full window
  88,001 (= night 26,256 + day 61,745; 44 lots are window_preset='other'
  post-close); seam range 06-30→07-08 per-date sum == totals.all exactly
  (280,548, one source per date); ice-era regression byte-exact (07-13 day
  44,951); 71/71 tests pass.

**Deployment state (current):** dashboard+API run locally via
Start_Session_Volume.bat (:5062) against Supabase. The vlmapi gateway's
/v1/sessionvolume/* routes read Supabase DIRECTLY (bar5m requires explicit
source= there — rule C lives only in this engine's query layer). railway.toml
is ready but NO Railway service exists yet for the Flask app — open decision.

**Next:** (1) Railway deploy decision for the Flask API/dashboard; (2) rotate the
Supabase DB password (several were pasted into chat 07-06/07 — current one was
set via setx without entering chat, but rotation still prudent); (3) consider
batching per-date profile queries if long ranges feel slow over Supabase.

## 2026-07-15 — client-facing branded PNG export

**Built:** "Download PNG" button on the dashboard, VLM navy/gold branding,
hand-drawn HTML5 Canvas2D (header bar + gold accent, KPI stat tiles, chart
re-plotted as canvas bars/lines, By Contract + By Trade Type tables, footer
bar), 2x scale for retina quality.

**Why this pattern, not Plotly's built-in export or html2canvas:** Lou's
actual reference ("options sandbox") turned out to be neither -- it's
`options sandbox/dashboard/templates/index.html`'s hand-coded canvas export
functions (exportStraddlePanel/exportTradePanel/exportSurfacePanel,
`_finishExport`), pure ctx.fillRect/fillText/lineTo calls, no library, at
SCALE=2. Matched that exact convention here rather than inventing a new one,
so VLM's client-facing PNGs stay visually consistent across dashboards.
Rejected: Plotly's displayModeBar camera icon (chart-only, no branding);
html2canvas of a hidden export div (untested fidelity vs the proven
canvas-drawing pattern); server-side Playwright render like
`options sandbox/dashboard/eod_png.py` (that module turned out to be a
DIFFERENT export path in that project, not what the reference screenshot
actually came from -- the real source was the canvas-drawing JS).

**Design call (Lou, verbatim):** "what ever the selectors are on the screen
is what it uses... no need for all of them every time...if i am comparing
one day...it prints one day...continuous selected...that is what prints...
simple." Implemented via a `_LAST` snapshot object captured at the same
point each view's render call fires (armPngButton), so the exporter reads
whichever view/data is currently on screen with no re-fetch and no mode
guessing. One `_pngSeries()` normalizer maps all 5 view shapes (single/
continuous/totals -> bars; overlay/ab -> lines) into one common shape so a
single `_pngDrawChart` serves every mode.

**Verification note:** this environment has no browser automation (no
Playwright/Selenium/node available in this session) and no JS runtime to
dry-run the canvas logic outside a real browser -- server-side checks
(function presence, balanced braces/parens, HTTP 200) were exhausted, then
Lou tested live in-browser and confirmed working ("perfect") before this was
committed. Keep doing this: for canvas/DOM-dependent features, be explicit
about what couldn't be verified server-side and have Lou confirm in-browser
before calling it done.

Committed + pushed: 807ecc1.

## 2026-08-01 — futures settlement price overlay + a real KC/CC/SB data-loss bug found and fixed

**Built:** settle + Open/High/Low overlay on the Daily Totals view. Source:
local `futures_settle_<date>.csv` at the ICE eod capture root
(`config.ICE_ROOT`) -- the SAME read-only source this engine already reads
for volume, not a network call. Rejected two other sources first: the VLM
Data Gateway's `oi_data.csv` (github passthrough, 53MB/~4.5s, T+1-only --
had no data for the exact date Lou needed it for, a Friday not yet settled
on the weekend he was writing a report), and options-sandbox's live snapshot
cache (only ~11 days of history, live/last not settle). `C:\Ice eod records`
turned out to have same-day settle+OHLC back to 2026-04-27 for all 4
commodities, already the root this engine's own blotter ingest reads --
Lou's push ("is vlm api not available for ice eod folder?") led to checking
the real source instead of assuming the gateway covered everything.

**Hard product requirement (Lou):** price must NEVER be fabricated,
interpolated, or defaulted. Missing data is a visible gap everywhere --
Plotly (`connectgaps:false`), the Daily Volume table (`—`), and the
hand-drawn Canvas2D PNG export (line breaks on null, never bridges). A
genuinely unreadable file is a real error, not silently treated as "no
data" -- but one bad file in a date RANGE must not blank out every other
date's real settle (found + fixed in the audit pass, see below).

**Real bug found in production data, not hypothetical:** while building this,
`contract_resolver.ice_to_generic()` was found to always resolve against
CT's hardcoded H/K/N/Z month table regardless of the `prefix` arg --
confirmed via live Supabase query that 100% of every KC/CC Sep(U) trade and
every SB Oct(V) trade had `generic_code=NULL` since ingestion began (0% of
every other month affected -- a clean, fully-explained pattern, not noise).
Fixed additively (`ice_to_generic(..., active_months=None)`, default
preserves CT's exact prior behavior, all 5 existing repo-root callers
unaffected -- verified against the full 86+71 test suite before and after).
Backfilled production: 183,277 `minute_agg` + 53,980 `bar5m` + 2,744,922
`ticks` rows recomputed for KC/CC/SB, independently re-verified against live
Supabase (not just the job log). Only remaining NULLs (SBN8/SBV8, far-dated
position-2/3+ contracts) are legitimately out of the generic-slot window,
confirmed by breakdown.

**Lou's call on scope (verbatim, when I flagged this as a blast-radius
question):** "why are we not fixing known bugs...this is a self contained
app...what blast radius" -- pushed me to actually check downstream impact
with real numbers instead of a general worry, which is the right instinct:
the fix itself was genuinely additive/safe, the backfill was the only part
with real blast radius (a write to already-migrated production data), and
once dry-run-verified clean it was fine to just run it. Lesson: "blast
radius" as a stop-and-ask trigger should be backed by an actual dry-run/
count, not treated as a blanket reason to defer -- Lou will push back
(correctly) if the caution isn't load-bearing.

**Bug found in review after "review, test and audit" (Lou, post-ship):**
dashboard's price fetch never sent the selected contract in its URL --
`priceq` used the same `dq` (date-only) params as before, so `contract=`
was never passed and price always silently fell back to front month
regardless of what was picked in the (multi-select) contract picker. Not
caught by the earlier adversarial-audit passes (they reviewed price.py's
internals, not the dashboard's actual fetch wiring against picker state).
Fixed: exactly one contract selected -> that contract's price; 0 or several
(incl. "all") -> front month (no single coherent price for an aggregated
multi-contract volume sum) -- confirmed Lou's own framing before building
("the price should match the volume query"). Lesson: audit passes that
review a module in isolation can miss integration bugs at the call site --
worth a live end-to-end HTTP-level check of the actual UI state -> fetch
URL -> response chain, not just unit-level correctness of the new module.

**Also fixed in audit:** a single unreadable settle file in a date RANGE was
raising `PriceUnavailable` and discarding every other date's already-read
real data in the same response (`settle_series` now catches per-date,
returns `errored_dates` so a genuine read failure is still visibly
distinguished from an honest "no file yet" -- never silently identical).
`.env` added to `.gitignore` (was untracked but unignored -- one broad
`git add` away from committing live secrets; never actually committed).

Committed + pushed: `3639e00` (resolver/ingest fix + backfill),
`64d1f14` (price overlay feature).

---

## Session: full-session bucket + continuous-view label fix (2026-08-11)

**What:** Added `bucket=full` to `/v1/sessionvol/{cmd}/profile` (dashboard
Bucket dropdown gained "Full session"). Collapses each session's whole
resolved window (Night 21:00→07:00 / Day 07:00→14:20 / custom) into ONE bar
per session — lets Lou trend night-total vs day-total volume across many
sessions (Window=Night/Day, Bucket=Full session, View=Continuous). Also fixed
Continuous view's on-bar timestamp text being illegible at density
(`textposition:'none'`; full timestamp still in hover, x-axis already has one
clean date tick per session).

**Why not just a large numeric bucket (e.g. 1440 min)?** `_fold()` floors by
clock-minute-of-day, so a large bucket on the Night window would silently
SPLIT at midnight into two wrong bars instead of one. Proved with a synthetic
midnight-crossing check before shipping: `_fold(rows, 1440)` returned
150+50 (split) where the correct answer is one 200-lot bar. New `_collapse()`
helper sums the ENTIRE queried range regardless of clock time and labels the
single row at the window's own start — the only correct way to do this.

**Rejected:** reusing `_fold` with a huge bucket_minutes value — looks
right for Day windows (never crosses midnight) but silently wrong for Night,
which is exactly the case Lou asked for ("if i want to see the night time").

**Scope check:** additive only — new `full` enum value alongside existing
1m/5m/15m/60m; `/profile` response shape unchanged; `bucket_minutes_effective`
seed-grain flag guarded against the new string sentinel. 71/71 tests pass.

**Deploy:** No Railway service is connected to `vlmsofts/vlm-session-volume`
(checked all 27 projects in the account — confirmed with Lou, not assumed).
Merged to `main` only (`c30e4ec`, ff from `feat/full-session-bucket`, branch
deleted after merge); Lou deploys/serves this dashboard by a process outside
Railway — ask him what it is if automating this matters later.

---

## 2026-08-20 — PNG export: nice-axis + timeframe stamp (display only)

**What:** Two purely cosmetic fixes to the Download-PNG export in
`ui/templates/dashboard.html`, both reported by Lou against the Daily Totals
view (the on-screen Plotly chart was already correct — only the exported PNG
was wrong).

1. **Axis was wildly too wide.** `_pngDrawChart` set the volume axis top with
   `Math.pow(10, Math.ceil(Math.log10(maxV)))`, halved once. Only powers of ten
   and their halves were reachable, so a 10,529 max snapped to a **50,000**
   axis and every bar sat in the bottom fifth. New `_niceAxis(v)` tries 4 AND 5
   gridline bands against a 1/2/2.5/5 x 10^n ladder and keeps whichever wastes
   least headroom: 10,529 -> **12,500 over 5 bands, 84% fill**, ticks still
   round (0/2.5k/5k/7.5k/10k/12.5k). Band count is no longer hardcoded — it
   flows from `_niceAxis` into the gridline loop (`BANDS`), so the price axis
   on the right stays aligned to the same bands.

2. **No timeframe on the PNG.** The export is shared standalone, so it now
   states its window in two places: the header subtitle
   (`CT — Cotton No. 2 · Daily Totals · 21:00 → 07:00 ET`) and a
   `WINDOW (EACH SESSION)` KPI tile, plus `AVG / SESSION` — matching the tile
   row the dashboard already shows. New `_pngWindow(w)` mirrors on-screen
   `windowStat()` logic exactly rather than re-deriving it.

**Why `_pngWindow` can't just print `w.window`:** in multi-day mode `w.window`
holds only the LAST session's bounds. Printing it raw across a range would
misstate the timeframe — so single session prints real bounds, multi-day
prints the repeating clock-time filter, same rule as `windowStat()`.

**Knock-on that had to be handled:** the new tiles pushed Daily Totals to 8
KPI tiles. At 7-across, `WINDOW (EACH SESSION)` needs ~130px against a 129px
budget — the label would have run through the tile border (only the *value*
font shrank to fit; the label never did). So: tile row wraps at 6 per row,
labels now shrink like values, and canvas height derives from the wrapped row
count (`KPI_BLOCK`) — without that the tables and footer would have run off the
bottom of the bitmap.

**Scope:** display only. No API shape, column, date convention, or query path
touched — zero blast radius. 86/86 tests pass (they cover the Python engine,
not the template; the template was verified against the live server).

**Also fixed — the reason this took two rounds:** `api/app.py` ran
`debug=False` with no `TEMPLATES_AUTO_RELOAD`, so Jinja served the
`dashboard.html` it compiled at startup and disk edits were invisible until
restart. See ERRORS.md. Added `app.config['TEMPLATES_AUTO_RELOAD'] = True`.

**Not verified by me:** no Node in this environment, so there is no headless
render of the actual canvas. Logic was proven by porting `_niceAxis` verbatim
to Python and by bracket-balancing the served script; the pixel output was
confirmed by Lou from the browser.

---

## 2026-08-24 — R11 applied: cancelled prints never count

**STATUS: BUILT, UNPROVEN LIVE.** Every claim below is from the test suite and
the local store. Nothing has run against a live session or a full replay.

**What was decided.** A Delete-tagged print is a busted trade, not flow, and
must never land in a default tally, chart, table or client-facing number. R11
already existed in the analyzer repo; this extends the same ruling here. ONE
ruling, not a second mechanism.

**Step 0 finding (checked before writing code).** `repository.window_sum`
ALREADY returned a `clean` figure excluding exactly `efs_delete`, with a
standing test. The data layer and classifier were correct the whole time. So
this was a wiring and labelling job, not a new mechanism. Third time in this
program the answer was already on disk.

**Where the rule lives.** `ingest/classifier.EXCLUDED_FROM_CLEAN` plus
`is_excluded()` / `clean_split()` / `excluded_sql()`. Chosen because the
classifier already owns the type vocabulary and both layers already import it,
so there is no upward dependency and no new module. REJECTED: putting it in
`store/repository.py` (would force ingest to import upward) and a new
`rules.py` (a module for one constant).

**Sites changed.** `rollup._window_sums` (the real defect: grouped by
window_preset with no type filter, so cancelled lots entered night/day/full);
`reconcile.build_reconcile`; `repository.traded_contracts`;
`repository._types_filter` (default clean, explicit dirty, which is what fixes
`profile` and therefore the intraday chart); dashboard headline flipped from
`totals.all` to `totals.clean` at 9 client-facing sites.

**Excluded accounting (P6.6).** No silent drops. `window_sum` returns
`excluded` + `excluded_by_type`; `_window_sums` returns per-contract
`excluded`; both emit functions report `excluded_lots`. Invariant asserted:
clean + excluded == all.

**`clean` keeps its name** but now carries a fuller meaning: all-in minus every
bucket R11 classes as cancelled, which today is exactly `efs_delete`. Stated in
the `window_sum` docstring so a future session does not have to infer it.

**Sidecar re-emission.** CT 2026-07-02 re-emitted, the entire measured
contaminated set. Session row full_total 27,858 -> 27,759; CTZ6 row full
18,366 -> 18,267 (day 14,699 -> 14,600). Verified by diff that ONLY those two
volume figures changed; the other four contract rows show only a `generated_at`
refresh. The shared VLM history files were NOT touched (blast-radius gate,
build plan section 7, confirmed: last modified 2026-06-26).

**Root cause of the drift.** `_window_sums` had NO test at all. That absence,
not the missing filter, is why the rollup layer diverged from the repository
layer. Now covered by `tests/test_r11_cancelled_never_counts.py` (13 tests),
which proves the guard fires on Delete AND does not over-fire on plain EFS or
EFP, and includes a guard against a second copy of the rule appearing anywhere
in the tree. That guard was itself sabotage-tested: injecting a duplicate rule
into repository.py made it go red, and it went green on restore.

**NOT done, deliberately.** `reconcile.build_reconcile` compares the tape
against `futures_settle` Volume, which is the PRIOR session's figure (measured
in C:\Ice eod records: 165 contract-days match D-1 exactly, ZERO match same-day).
It therefore grades today's tape against yesterday's volume. Commented in place,
queued as separate work; the field belongs to another repo.

**Suite: 84 passed, zero red.**

---

## 2026-08-24 -- R11 WIDENED TO THE DELETE TAG (Lou's ruling)

**STATUS: BUILT, UNPROVEN LIVE.** Measured from stored data and the CT blotter
corpus. No live session has run through the widened ladder.

**Decided.** Exclusion keys on the Delete TAG, not on the bucket name
`efs_delete`. Delete is orthogonal to trade type: a cancelled block is
cancelled. The old rule missed 340 of 1,262 cancelled lots in the CT corpus
(`BlockTrde, Leg, Delete` 186, `BlockTrde, Delete` 127, `EFP, Delete` 22,
`Leg, Delete` 5) purely because the ladder puts BlockTrde above Leg above the
aggressor tags. That was an implementation artifact, never the ruling.

**Implementation: a cancelled bucket per base type**, not a boolean column.
REJECTED the boolean because `minute_agg` and `bar5m` are keyed on
`(.., primary_type)` ONLY -- there is no tag column at that grain, so a
cancelled block aggregated there is INDISTINGUISHABLE from a live block and no
SQL predicate can recover it. Proven on a live rebuild before choosing. Carrying
the tag in the bucket is what makes the exclusion expressible in the aggregate
tables at all, and it keeps cancelled volume attributable per type.

**`efs_delete` KEPT as-is.** Historic name, stored on disk in ticks/minute_agg/
bar5m, and the target of existing `types=['efs_delete']` queries. Renaming it
would have invalidated every stored row.

**Retrievability (req 1):** `classifier.CANCELLED_TYPES` addresses the whole
set; `/catalog` DERIVES its type list from the constant rather than hand-listing
it, so a future widening cannot leave cancelled volume unadvertised.
`is_cancelled` is an alias of `is_excluded`, not a second rule.

**bbg_map `*X`:** now tag-keyed too, EXCEPT bare `'*X'`, which stays
`efs_delete` deliberately -- the 07-02 reconciliation verified it at 99 == 99
lots, all EFS busts, and bar5m stores no raw conditionCodes, so mapping bare
`*X` to outright_delete would be an inference, not a measurement. Evidence over
inference.

**Checksum re-run (req 4):** 19 FLAG / 14 flagged contract-days -> 18 FLAG / 12,
OK 17 -> 18. The two resolved are 2026-07-07 CTZ6 (-108) and 2026-08-03 CTZ6
(-8), BOTH cancelled-BLOCK cases. The other 12 are unaffected -- they are tape
deficiencies or the open unexplained set, not cancellation.

**Nothing moves (req 5):** the store's 5 CT sessions contain no cancelled
non-EFS prints, so recomputing every sidecar row reproduces the written figures
exactly (verified by recomputation, not assumed). Nothing re-emitted. The
Bloomberg bar5m seed keeps 8,054 lots under `efs_delete` from the old map,
unchanged until re-seeded.

**CORRECTS THE PHASE 1 REPORT.** It used a substring test (`'Delete' in cond`)
and so credited R11 with resolving 07-07 CTZ6 and part of 08-03 CTZ6. Both were
cancelled BLOCKS the bucket-name rule never touched. Corrected in
`SAME_DAY_VOLUME_CUTOVER.md`.

**Suite: 115 passed** (24 new in `test_r11_keyed_on_the_tag.py`,
sabotage-verified: reverting to the narrow rule turns 10 red). Two superseded
assertions updated with the reason on record. Nothing committed.

---

## 2026-08-24 -- SAME-DAY INGEST: 15:00 primary added, 17:10 kept as backstop

**STATUS: BUILT, PARTIALLY PROVEN LIVE.** The batch ran live by hand (exit 0,
all 4 commodities, zero rows added). The 15:00 TASK HAS NOT YET FIRED ON ITS
OWN -- the first live 15:00 run is what proves it. Full detail:
`RUNBOOK_INGEST.md`.

**Corrected a premise before acting.** The order stated nothing schedules
jobs.daily_ingest. It IS scheduled and working: `VLM ICE Timesales Engine -
Daily Ingest` has run daily at 17:10 since 2026-07-06 with zero missed runs,
writing all 4 commodities to Supabase (verified by EFFECT -- Supabase current
through 2026-08-21 with per-file sha256 rows -- not by the exit code, since the
batch wrote no log). The T+1 was the 17:10 timing alone, which Lou confirmed.

**Decided: 15:00 primary + 17:10 backstop.** Both run the same batch. 15:00
clears the 14:22-14:25 blotter and the ~14:41 futures settle with margin and
lands the session ~2h10m earlier. 17:10 keeps its ~2h45m margin for late or
recovered captures.

**REJECTED: an event watcher on the pulse's completion commit.** ~35 min
earlier than 15:00, not worth a new resident process; a clock task with a
backstop has no daemon to die silently. Parked, not rejected outright.

**Contention gate cleared BEFORE registering.** 15:00 sits inside the
13:35-15:30 ICE contention window, so this was the blocking question. Verified
across all 19 repo modules daily_ingest imports: NO win32com, NO pythoncom, NO
Dispatch, NO ice.get_*. File-only. The one network call is a Cloudflare purge,
unconfigured on this box (immediate no-op), timeout-capped and exception-
guarded when configured. Costs the shared ICE COM session nothing.

**Two real defects found and fixed in the batch.** It wrote NO log despite its
own comment saying to check one, and ended `exit /b 0` unconditionally -- a
commodity could fail every day while Task Scheduler reported success. Now:
per-commodity [ OK ]/[FAIL], full output to `logs/run_daily_ingest_all.log`,
and a real exit 1 naming the failures. All three paths TESTED, including a
sabotaged copy forcing every commodity to fail (exit 1, each named). Two cmd
parser traps were caught by that testing: an unescaped `(` in a log argument
broke the block and returned 255 instead of 1, and empty `echo` printed
"ECHO is off." -- both fixed.

**Added `is_trading_day.py` to this repo.** daily_ingest DOES guard holidays but
tests the SESSION date, not TODAY, and with --date omitted takes the latest
day-folder -- so a Saturday run would re-process Friday. Deliberately a separate
thin copy rather than importing from the capture repo: one calendar
(config.CLOSED_DATES), two callers, no cross-repo path dependency.

**Idempotency proven on a real day, not re-derived:** re-running all 4
commodities added ZERO rows (CT 1155932, KC 2000476, SB 3082497, CC 1900124
unchanged). sha256 skip + ON CONFLICT DO NOTHING + delete-and-reinsert aggregates.

**MEASURED and recorded, correcting the order's ~15:32 figure:** settled_surface
lands anywhere from 14:59 to 17:09 (08-18 14:59, 08-17 15:14, 08-20/21 15:32,
08-19 17:09). So the 15:00 run is FUTURES-COMPLETE and OPTIONS-PARTIAL, and
"~15:32" must not be relied on. Futures volume -- all this ingest computes -- is
unaffected; the pipeline never reads a surface.

**Suite: 115 passed.** Nothing committed.

---

## 2026-08-24 -- AGGRESSOR SIDE IN THE ARCHIVE (vendored, three-state)

**STATUS: BUILT, UNPROVEN LIVE.** No real session has yet written side rows to
Supabase, and the live minute_agg/bar5m tables do NOT yet have the column -- see
"migration still owed" below.

**STEP 0 finding, which overturned an assumption.** The analyzer's
`outright_flow.aggressor_side()` returns FOUR states, not two or three:
BUY / SELL / UNK / CROSS. It is a PURE token-to-side map -- no ruling,
hierarchy or vol-tell behaviour, so there was no ruling layer to strip. It
takes a RAW Conditions STRING (substring `in` tests), not a token set, unlike
this engine's classifier which works on a frozenset.

**Decided: three states, one unsided bucket.** buy / sell / unsided.
CROSS collapses to unsided because RFCCross marks a BROKER-EXECUTED CROSS --
an execution mechanism, not a side. A cross has a buyer and a seller; the
aggressor is obscured because the broker held both sides. So CROSS is a REASON
for unsided, never a fourth side. REJECTED (Lou, addendum 2) a second unsided
bucket or a 'crossed' state: rare-instance, not worth the plumbing. An
unrecognised token FLAGS via `aggressor.unexpected_tokens()` instead.

**MEASURED, not assumed:** RFCCross is absent from all 375 CT futures blotter
files. The complete futures Conditions vocabulary is 7 tokens forming 12
strings (Leg 616,984 / SetByBid 607,235 / SetByAsk 600,223 / blank 365,887 /
EFS 263 / EFP 209 / BlockTrde,Leg 118 / five Delete variants). RFCCross is an
OPTIONS-tape token; this engine reads futures.

**Q2 ruling applied: aggressor.py CONSUMES classifier.outright_side()** rather
than re-testing the tokens. The TOKEN test lives in classifier, the
TAG-TO-SIDE map lives in aggressor. One home each, no duplicated matching --
which is what the no-second-copy guard protects.

**Vendored copy, not a cross-repo import** (same shape as is_trading_day.py):
an import would make the engine unrunnable without the analyzer, breaking the
Railway deployment. `tests/test_aggressor_matches_analyzer.py` is the
mechanical drift guard -- it loads the analyzer's function BY FILE PATH (not as
a package, which would drag in pipeline.ice_attribute and fail outside that
repo) and SKIPS LOUDLY when absent. Proven: 17 skipped / 0 failed with the
analyzer missing.

**side is ADDITIVE.** primary_type is untouched -- outright is still ONE
primary_type, not three. side joined the PK of minute_agg and bar5m. Only live
outrights carry a real side; leg/efs/efp/block and every cancelled type are
'unsided' by construction, ASSERTED not assumed. The Bloomberg bar5m seed
writes 'unsided' throughout: its conditionCodes carry no aggressor stamp, so a
side there would be invented rather than measured.

**Sabotage, both directions, both proven:**
  * inverting the map -> 3 tests red in the local suite, 4 red in the
    cross-repo guard; green on restore.
  * the no-second-copy guard FAILED ITS FIRST SABOTAGE -- a single-line regex
    missed the realistic multi-line `if 'SetByBid' in cond: return 'buy'`
    shape. Widened to a multi-line window with comment lines blanked; now
    catches it naming store/repository.py:57, green on restore. A guard that
    cannot go red is not a guard.

**Existing callers unchanged, proven:** no query in repository.py or
routes_query.py references `side`, and all 159 tests pass with side in the key.

**Schema trap hit and fixed:** a semicolon inside my new schema.sql COMMENT
split the DDL mid-statement (db.init_schema splits on ';'), producing 39
errors. The file header warns about exactly this. Fixed; splitter re-verified.

**REBUILD COST, MEASURED** on CT 2026-08-21 (27,665 ticks) against a local
sqlite copy: minute_agg 5,223 rows in 0.12s, bar5m 2,222 rows in 0.02s =
0.14s per commodity-day. 265 commodity-days -> ~0.6 min single-threaded
locally; Supabase round-trips will dominate in production. ticks is permanent
and keeps conditions_raw, so side is always re-derivable and nothing is lost.

Real-day sanity (CT 2026-08-21 outrights): buy 11,869 / sell 12,297 /
unsided 4,036, summing exactly to the 28,202 outright total.

**MIGRATION STILL OWED (not done, flagged):** `db.init_schema()` is
CREATE TABLE IF NOT EXISTS, so it will NOT add the column to the LIVE Supabase
minute_agg/bar5m, which currently lack it. A live ingest would fail on the new
INSERT until an ALTER TABLE runs. Needs a ruling on ALTER-plus-backfill vs
rebuild.

**Item 5 (volume-at-price endpoint) NOT built** -- separate piece, deliberately
not started so the archive change lands clean.

**Suite: 159 passed.** Nothing committed.

### 2026-08-24 (same day) -- side MIGRATION EXECUTED on Supabase

**Ruling (b): ALTER + FULL REBUILD.** Rejected (a) ALTER-plus-default because
'unsided' would then mean two things -- genuinely no aggressor stamp, or row
predates the column -- with no way to tell them apart afterward. Same
bucket-holds-two-animals defect as Leg and outright.

**BLOCKED FIRST, and stopping was correct.** The ALTER failed twice at the 2min
statement_timeout. ADD COLUMN with a NOT NULL DEFAULT is catalog-only in modern
Postgres and should take milliseconds, so 120s meant WAITING, not working. Cause:
pid 329945, idle in transaction for 1h50m, holding AccessShareLock on both
tables. Filed as DEFECT_IDLE_IN_TRANSACTION.md -- root cause is api/_db()
returning one long-lived connection with NO commit/rollback/close on any read
path (grep-verified, zero matches), so every SELECT leaves a transaction open.
NOT FIXED, deliberately: one session, one concern.

**Executed after pre-checks:** confirmed still idle-in-transaction, still the
freshness read, and the ONLY conflicting lock holder. Terminated, then ALTERed
with lock_timeout=5s so a new blocker would fail fast rather than queue (a
waiting ALTER blocks everything behind it). minute_agg 5.4s, bar5m 8.1s.

**Rebuild: 265/265 commodity-days, zero failures, 10.3 min (2.33s each).** The
local estimate was 0.14s/day; Supabase round-trips dominate as flagged.

**VERIFIED, all pass:**
  * no side value outside the 3 states, either table
  * 551,506 minute_agg outright rows carry a REAL derived side
  * 0 non-outright rows carry a side
  * conservation buy+sell+unsided == outright total: 265/265 commodity-days
  * bloomberg seed 189,260 rows / 10,162,374 lots ALL unsided, as designed
  * CT 2026-08-21 spot check exact: 11,869 / 12,297 / 4,036
  * vs the pre-migration snapshot, ZERO primary_type totals moved -- the
    rebuild changed the KEY, never the VOLUME

**Live ingest confirmed against the migrated schema** before the scheduled
runs: single-commodity and the full 4-commodity batch both exit 0, and
verification stays green afterward.

Suite: 159 passed.

---

## 2026-08-24 -- FIXED the idle-in-transaction leak (autocommit)

Lou withdrew the earlier "file, do not fix" ruling: the parked item was
"freshness() leaks one transaction", but the investigation found NO read path
in api/ ever commits, rolls back or closes -- a larger defect that should not
have inherited the parked status.

**Chosen: (b) AUTOCOMMIT via a read_only flag. Rejected (a) rollback-on-
teardown.** The API never writes (12 read call sites, zero exec/execmany/
commit, grep-verified). There is no transaction worth keeping open, so the
honest fix is to not open one. A teardown hook would still open a transaction
per request and would depend on every future path remembering to route through
it -- the same "everyone must remember" class that caused the defect.
Autocommit is a property of the CONNECTION, so it covers all 12 paths and every
path added later.

`store/db.py`: Db(read_only=False) / connect(read_only=False); read_only opens
psycopg with autocommit=True. `api/app.py` is the ONE opt-in caller. All three
ingest jobs call bare connect() and keep the transactional default their
delete-and-reinsert idempotency needs.

**Found while probing, sharper than the earlier report:** the connection was
already idle-in-transaction BEFORE any read -- init_schema() itself left it
open. Autocommit fixes that too.

**Proven through the REAL Flask app** (create_app + test client), observed from
a separate connection: after driving every real route, state='idle',
in_transaction=False, locks on minute_agg/bar5m NONE, and 0 idle-in-transaction
sessions database-wide. Before the fix the same probe showed 'idle in
transaction' with AccessShareLock on both tables.

**Endpoint behaviour unchanged:** /window returning 400 without preset is
PRE-EXISTING (preset is required; with preset=day it is 200), not caused by
this change. A real write path (daily_ingest CT) still runs correctly.

**BLOAT CHECKED, NOT MATERIAL, NO VACUUM RUN.** minute_agg and ticks are at 0
dead tuples; bar5m 19,978 dead / 3.1%; the two small tables show 17-23% on
trivial counts (412 and 67 rows). Autovacuum ran on both large tables TODAY at
15:41/15:43, minutes after pid 329945 was terminated -- consistent with the
leak having pinned the xmin horizon and vacuum catching up once released.

**(d) idle_in_transaction_session_timeout NOT touched**, still parked -- it is
a server-level decision.

Suite: 165 passed (6 new, sabotage-verified: removing read_only=True from
api/app.py turns test_create_app_builds_a_read_only_db red).

---

## 2026-08-24 -- SAME-DAY SESSION CARD: honest basis, leg split, blanks ruling

**STATUS: BUILT, UNPROVEN LIVE until Lou has looked at the PNG.**

**Item 5 cost gate answered BEFORE building.** First attempt used a LEFT JOIN:
5.84s AND WRONG (10,785 "paired" on Z26 vs a true 1,055). A join counts
row-PAIRS -- one print matching ten same-second prints counts ten times. The
question is per-PRINT, which is EXISTS: 0.32s for the same session, 18x faster
and correct. Recorded in a comment in session_render.py so nobody reaches for a
join again.

**Definition D shipped (Lou's ruling):** a SPREAD LEG is a leg print with a
same-second, SAME-SIZE leg partner on another contract in the same commodity;
an OPTION HEDGE is a leg print without one. Ship the definition the proof rests
on -- the evidence that paired legs are spread legs is size-matched pairs
pricing inside the published spread day range, 400 of 400.

**The 1,033/57 figures were WITHDRAWN, not reproduced.** They came from a hand
count that aggregated by SECOND rather than per print, so a second containing a
mix of paired and unpaired prints fell entirely to unpaired. The per-print cut
is correct. Z26 08-21 ships as 1,055 spread legs / 2,171 lots and 35 option
hedges / 1,045 lots. A future session should NOT go looking for a prior source
for 1,033/57 -- there is none, and the number is wrong.

**The JOIN-vs-EXISTS finding was made in this repo while building this**, not
inherited from anywhere. Recorded in the session_render.py docstring under
PROVENANCE so the credit and the absence of an earlier source both travel with
the code.

**Every one of Lou's figures reproduces exactly:** base 20,353, buy 48.6 /
sell 51.4, clips 1.42 vs 1.31, unsided 1,203, night 54.3% buy flipping to day
46.4%. Leg totals 1,090 prints / 3,216 lots on both partitions.

**Blanks ruling implemented as stated:** IN the total, OUT of the aggressor
base, NEVER prorated, with their own named block on the card.

**Item 3 needed NO new machinery and NO cross-repo read.** The engine already
holds the tape side and ingest.discover + ingest.reconcile already read the
settle file. completeness() lives in the render job.

**Verdict paths PROVEN with a synthetic post-cutover settle file** (temp dir,
no production file touched), because every settle file on disk still predates
the CumVolume cutover so real data can only produce NOT YET CHECKED. OK and
FLAG both verified; FLAG names the contract and difference: 'CTZ6 -1,500 lots',
the 2026-08-19 shape exactly.

**FOUR RENDER DEFECTS CAUGHT BY EYEBALLING THE PNG**, none of which a test
would have found: the window printed 14:19 (the last print) and read as a
truncated capture -- now prints the real 14:20 boundary; a Share column sat
under a Prints header; the NOT COMPARABLE verdict was a dead end with no
number -- now NOT YET CHECKED with the reason; a wrapped sentence left an
orphan line.

**Conservation asserted in code, not just tested:** spread+hedge == leg total
(prints AND lots), buy+sell+unsided == outright total.

**Sabotage both ways:** dropping the size match turns 3 red; folding blanks
into the aggressor base turns 6 red. 180 passed on restore.

**renders/ gitignored** -- point-in-time artifacts, reproducible from the store.

Item 5 (volume-at-price) still NOT started, as ruled.
