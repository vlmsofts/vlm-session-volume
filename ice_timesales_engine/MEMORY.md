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
