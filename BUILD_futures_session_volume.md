# BUILD AUTHORIZATION — Futures Session-Volume (consolidated, single source)

This is the one document to build from. It supersedes the earlier scope/feasibility/investigate notes
(those remain as background). **Gated build with stop-points; confirm the proof gate before merging the
capture change. Do not skip the stop-points.**

Everything below was decided with the owner. Hard rules are non-negotiable.

---

## ARCHITECTURE — two pieces, deliberately split (keep the analyzer small)

```
Options_flow_analyzer/   (EXISTING repo — touch as little as possible)
  price_tape.py          → + a small sidecar write (the ONLY change here)
  data/<date>/ct_futures_volume.csv   → NEW data file (the capture)

vlm_session_volume/      (NEW separate repo — all the bulk lives here)
  config.py              → self-contained; read-only path to the analyzer's data/
  futures_session_volume.py   → the engine (windows, RVOL, symbology, report)
  symbology.py           → self-contained generic↔ICE↔year resolver (ported, extended)
  data/history/futures_session_volume_history.csv   → PERMANENT, append-only
  data/<date>/futures_session_volume.{txt,json}
  tests/...
  (later) api_publish.py → pushes history to the GitHub source the VLM gateway reads
```
The new repo **only reads** the analyzer's `data/` folder and **writes nothing** into the analyzer.
The analyzer's sole change is the additive sidecar write.

---

## PART A — Capture stub (in Options_flow_analyzer) — minimal, additive

**Change:** `price_tape.py` also writes `data/<date>/ct_futures_volume.csv`, recording each futures
contract's **`volume` and `oi`** from the RTD feed (both already present in the feed; currently
discarded).

**Cadence — forced boundary flush (decided):** write all live contracts' current cumulative
`volume`+`oi` once at each of three boundary moments per session — **~21:00 (session open),
07:00, 14:20 ET** — by forcing a write at the first poll on/after each boundary minute (independent
of the price-tick dedup). Three readings give night/day/full exactly **without assuming** the
21:00 volume reset — but also CONFIRM whether futures cumulative volume resets at 21:00 and record
the finding in the new repo's MEMORY.md.

**Sidecar schema:** `timestamp, date, commodity, contract, boundary(open|0700|1420), volume, oi`.

**Boundary capture (simple + reliable — decided; do NOT overengineer).** The RTD `volume` is the
session **cumulative** counter, so 20-second poll granularity is irrelevant: night/day/full are just
differences of the counter read at each boundary, and a 20s gap loses nothing (the next reading already
includes it). Requirement: fire on the **first poll on/after** each boundary (not only inside the exact
`:00`/`:20` minute), once per boundary (existing latch). **Implement via absolute boundary
datetimes built from the session date** — open = prior-evening 21:00, 0700/1420 = session date —
and compare `now >= boundary_dt`. Do **NOT** use a naive `(hour,minute) >=` tuple: the session wraps
past midnight, so `21:00 ≥ 07:00` and `21:00 ≥ 14:20` as tuples would mis-fire the morning boundaries
at the evening open. (The code already derives the session date for its date-roll.) The recorded `timestamp` shows how close to the
boundary the reading landed — that is enough; **no `staleness`/`reliable` columns, no loud-flag
machinery.** A genuine multi-minute outage straddling a boundary is already logged (`SIDECAR_SKIP`) and
shows as a missing/late row the engine can spot. Three rows per contract per day; nothing
20-second-granular is stored.

**Timezone (verified):** use naive `datetime.now()` minute comparison, matching the existing loop
convention (the module already assumes machine clock = ET; owner confirmed machine IS US Eastern).
Do NOT introduce ET-aware time only for the sidecar — it would risk firing on a different boundary
than the session-end/backup logic. Naive-on-ET tracks wall-clock 21:00/07:00/14:20 through DST
automatically. Use a **once-per-boundary latch** per session so each boundary writes exactly one row.

**Zero-collision constraints (already verified — keep them true):**
- Do **not** add columns or rows to `ct_price_tape.csv`, its history, or its backups. Volume goes
  **only** to the new sidecar file. (This is why no GEX/row re-proof is needed.)
- The sidecar is a brand-new filename; nothing else reads it; no glob/cleanup touches it.
- Existing `price_tape.py` behaviour (price rows, dedup, backup, history) is unchanged.

**Tests + PROOF GATE (must pass before this change merges):**
- Add a unit test for the sidecar write (boundary flush fires once per boundary; schema correct).
- Run the **full** `pytest` suite — confirm green (existing price-tape tests compare to the live
  `_FIELDS`, which is untouched, so they must stay green).
- Dry-run / diff proof: confirm **no existing analyzer file changed** — only `price_tape.py` (code)
  and the new sidecar data file. **STOP and show the owner this proof before proceeding to Part B.**

---

## PART B — Engine (new repo `vlm_session_volume`)

**Self-contained config (`config.py`):** window times (21:00 / 07:00 / 14:20), lookback tiers
(5,10,20,30,60), CT closed-dates (IFUS 2026), and a **read-only** path to
`…/Options_flow_analyzer/data`. Imports nothing from the analyzer.

**Data sources (ARCHITECTURE — corrected 2026-06-17):**
- **Bloomberg = ONE-TIME reseed (history only).** The `cotton_futures_volume_history_blpapi.py` pull
  already ran once and built the deep full-session history (5,398 sessions, 2005→present), verified vs
  ICE official. It is **NOT a daily job.** Its only forward role is the historical baseline for the
  trailing 5/10/20/30/60 RVOL and the December-over-years view. (Re-run only to extend/repair history.)
- **ICE RTD = the DAILY forward source.** Part A's `price_tape.py` sidecar (`ct_futures_volume.csv`)
  captures each contract's cumulative volume at the three boundaries. So **night, day, AND full all come
  directly from the RTD sidecar each session**: night = cum@07:00 − cum@open, day = cum@14:20 − cum@07:00,
  full = cum@14:20. No Bloomberg in the daily path.
- `oi_data.csv` is not used (low-vintage undercount; superseded by the Bloomberg reseed for history).
- **RVOL deep tiers:** real now via the Bloomberg seed; graceful `n/a (have M of N)` only if a tier
  ever lacks history.
- **Seed ingestion / parsing (engine side):** the `HistoricalDataRequest` output is already flattened
  to a long table (`date, generic, volume, …`) by the puller. The engine then (a) loads it into the
  permanent full-session history, and (b) **attaches the actual contract + delivery_year per date** via
  the symbology module — a Bloomberg generic (`CTDEC1`) is a continuous front-December series, so the
  daily volume is the correct year-over-year axis, but the per-contract/`delivery_year` label must be
  resolved from the calendar (CTDEC1→CTZ6 in 2026, →CTZ5 in 2025, …). Volume has no roll-gap issue
  (per-day per-contract); only prices would.
- **Bloomberg reseed verified (2026-06-17):** the one-time pull was checked vs the **ICE official
  Futures Daily Market Report (12-Jun-2026)** — thin months exact, active fronts within ~2-4% (ICE's
  TAS/TIC/strategy inclusion vs Bloomberg outright `PX_VOLUME`). So the seeded **deep history** is
  trustworthy. oi_data not used.
- **Sourcing (CORRECTED & LOCKED):** **deep history = Bloomberg reseed (one-time);
  daily forward = ICE RTD sidecar.** Forward, the sidecar gives night/day/full **directly** from the
  boundary cumulatives — there is NO daily Bloomberg pull and NO "share × Bloomberg full" step.
  (Minor, accepted: a small history→forward vintage seam between Bloomberg-final and RTD-close that
  recedes as forward RTD sessions fill the trailing window.)

**Windows (per futures contract):** night = cum@07:00 − cum@open; day = cum@14:20 − cum@07:00;
full = cum@14:20 − cum@open.

**Universe (HARD):** futures months **DEC/MAR/MAY/JUL only, 1st & 2nd generic** (8 slots). **October
(`CTV`) and August (`CTQ`) excluded — never in any total, lookback, or output.** Serials don't exist
on the futures feed, so nothing to fold.

**Symbology (`symbology.py`, self-contained):** port the small pure functions from the analyzer's
`contract_calendar.py` and **extend to H/K/N/Z at positions 1 & 2** (the analyzer's version is
December-only). Every row stores `generic_code` (CTDEC1) + `ice_code` (CTZ6) + `month_code` +
`month_name` + 4-digit `delivery_year`. Resolve generic position by year-sorting the live contracts of
each month (front=1, next=2); positions ≥3 → `in_universe=0`.

**The four over-time comparisons** (each vs trailing 5/10/20/30/60 sessions, graceful
`n/a (have M of N)`):
1. **Night** vs its history.
2. **Day** vs its history.
3. **Night-vs-Day**, tracked over time: store `night_share = night/full` and `night_day_ratio =
   night/day`; compare each to its trailing history; flag unusual overnight tilt.
4. **Full** vs its history.
Report at both aggregate (8-slot total) and per-contract level. RVOL HIGH ≥2x / LOW ≤0.5x flags.

**Permanent history (`data/history/futures_session_volume_history.csv`):** append-only **forever**,
never cleaned; idempotent purge per (date, commodity) before re-append. One session-level file +
a per-contract companion (`…_by_contract.csv`) carrying the symbology key — REQUIRED for
December-over-years.

**Loud failure:** any genuine error (missing/unreadable source, unwritable history, bad config) →
non-zero exit with the **absolute path** of the offending file. Holiday → clean exit 0, no write.
Tolerate a single malformed/partial row only, logging a WARNING with file path + line.

**Schedule (new repo's own jobs):** Morning ~07:15 ET → night read; EOD ~14:35 ET → full report
(both windows + 4 comparisons). Stand down the OLD options `session_volume.py` jobs **after** the new
futures EOD job logs one clean live session (keep the options code, retire the jobs).

**Tests + backtest:** hermetic unit tests (window math, universe filter, symbology incl. non-Dec,
RVOL degradation, night-share/ratio, holiday guard, idempotent+permanent history, loud-fail-with-path).
Backtest `--no-write` over available sessions before enabling jobs.

---

## PART C — API publish (LATER phase, after B is proven)
Publish the new repo's history to the GitHub source the VLM gateway reads, then add the
`/v1/flow/CT/session-volume/*` routes per `PHASE3C_API_session_volume.md` (gex-aligned daily/latest/
history; standard envelope; github_reader; gateway is a separate prod codebase → additive routes only).

---

## BUILD ORDER + STOP-POINTS
- **A.** Capture stub + tests + PROOF GATE → **STOP, show owner the no-change proof.**
- **B.** New repo engine + symbology + history + backtest (`--no-write`) → **STOP, show sample report.**
- **B-jobs.** Register the two jobs; run parallel with options jobs 1 session; then retire options jobs.
- **C.** API publish + routes (separate approval).

## FINAL CONFIRMATIONS (report at each stop)
(a) zero existing analyzer files changed except the additive `price_tape.py` sidecar write;
(b) full `pytest` green; (c) exact paths of every new file + the two jobs; (d) Oct/Aug excluded and
8-slot universe enforced; (e) permanent history is append-only and idempotent.

## HARD RULES (recap)
- October & August excluded, everywhere, always.
- 8-slot universe (DEC/MAR/MAY/JUL × 1st/2nd generic).
- Permanent, never-deleted history.
- Loud-fail with the offending file path.
- The analyzer gets ONLY the sidecar write — nothing else.
- No code until the owner's verbal all-systems-go; Part A proof gate before any merge.
