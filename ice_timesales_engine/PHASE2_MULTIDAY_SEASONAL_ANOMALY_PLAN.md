# PHASE 2 PLAN — Multi-Day Continuous Chart · Seasonal Chart · Anomaly Flagging

> **Planning artifact only — nothing here is built.** Authored 2026-07-04 against the
> live repo state (commit `73eff25`, post blank→outright fold + reconcile-UI removal,
> pre 45-day backfill). Every claim in section 1 was verified by reading the cited file
> this session. NOTE: the classifier/D-1 descriptions below were written moments before
> commit `73eff25` landed; the two spots that describe the pre-fix state are annotated
> inline as RESOLVED — the architecture is unaffected.
> Build gates on: (a) Monday 2026-07-06 full 45-day backfill, (b) a few clean daily-ingest
> sessions proving the aggregation.

---

## 1. Verified current state (what phase 2 builds on)

| Component | File | Verified facts phase 2 relies on |
|---|---|---|
| Tick store | `store/schema.sql` → `ticks` | PK `(commodity, session_date, ice_code, seq_num)`; naive-ET ISO TEXT timestamps (lexicographic = chronological); `generic_code` nullable; `window_preset` ∈ night/day/other |
| Hot path | `store/schema.sql` → `minute_agg` | 1-min buckets per `(commodity, session_date, ice_code, minute_ts, primary_type)`; index `ix_minute_cmd_time (commodity, minute_ts)` already supports **cross-day range scans** |
| Query repo | `store/repository.py` | `window_sum` / `profile` take arbitrary `[start,end)` ISO bounds — they are **not** single-day-limited; `profile` folds 1-min → N-min in Python; `available_dates`, `freshness` exist |
| Windows | `api/windows.py` + `ingest/normalize.py` `window_bounds` | night `[D-1 21:00, D 07:00)`, day `[D 07:00, D 14:20)`, full = union; imported from `VLM_Session_Volume_Project` via `config.py` re-exports (never re-hardcoded) |
| API | `api/routes_query.py` | `/v1/sessionvol/{cmd}/window` already accepts `from=&to=` (loops `win.resolve` per date, returns `per_date`); freshness envelope (`source/stale/stale_age_seconds`) on every response; cache tags `sv:{cmd}:{date}` via `api/cache.py` |
| Classifier — **actual state (commit `73eff25`)** | `ingest/classifier.py` | `primary_type()` returns one of: `efs_delete, efp, efs, block, leg, outright, other`. Blank-condition prints fold into `outright` (a genuine unstamped fill); ask/bid/unstamped are an informational `outright_side()` sub-split, NOT separate primary buckets. `routes_query.py` no longer synthesizes `outright` from ask+bid. **D-1 RESOLVED** — the pre-fix description that was here is obsolete. |
| Rollup sidecar | `ingest/rollup.py` | Writes `data/history/futures_session_volume_history_ICE.csv` (+ by-contract) — session grain night/day/full per contract, `_upsert_csv` keyed replace; **never** the shared VLM files |
| Daily job | `jobs/daily_ingest.py` `ingest_day()` | Ordered steps: holiday-check → files → minute_agg rebuild → block supplement → reconcile → rollup → CF purge. Phase-2 precompute hooks in after rollup (step 6.5) |
| Backfill | `jobs/backfill.py` | Iterates day folders **ascending** — trailing-window stats computed in-order are correct on backfill |
| Resolver | `VLM_Session_Volume_Project/contract_resolver.py` | `resolve_generic` / `ice_to_generic`; roll rule = **generic rolls on the 1st of the delivery month** (`delivery_start = date(year, month, 1)`); verified live: N7 = CTJUL2 on 07-01, CTJUL1 on 07-02. **No first-notice / last-trade dates exist anywhere in the resolver.** `parse_ice_code(ice, as_of)` disambiguates the single-digit year using as_of |
| RVOL convention | `VLM_Session_Volume_Project/config.py:30`, `futures_session_volume.py:282` | `LOOKBACK_TIERS = (5, 10, 20, 30, 60)` sessions; `compute_rvol(current, prior_values)` → per-tier `{avg, rvol, n, note}`; degrades gracefully: `n < tier` still returns a value with `note='n/a (have n of tier)'`; display flags at **rvol ≥ 2.0 and ≤ 0.5** (`_rvol_line`) |
| Month sets | `commodity_meta.py` | CT `HKNZ`, KC/CC `HKNUZ`, SB `HKNV` (**no Dec**) — any seasonal month picker reads this map |
| UI | `ui/templates/dashboard.html` | Single-page Flask/Plotly dashboard; VLM palette navy `#1a1a2e` / gold `#c9a227`; reconcile-label column already removed |
| Data depth | `MEMORY.md` | Only 07-01 + 07-02 ingested today; ~45 day-folders land Monday. Latency budget: median 3.3 ms on minute_agg — phase 2 must stay in that class |

---

## 2. Cross-cutting design decisions

**One new base table feeds all three features.** C1 needs fast per-session series, C3
needs per-session/per-window/per-type totals, C2 needs per-contract daily totals. All
three collapse onto a single session-grain aggregate:

```sql
-- Session-grain totals: one row per contract x window x primary_type per day.
-- Rebuilt per day (delete+reinsert, same pattern as minute_agg). ~150 rows/day for CT.
CREATE TABLE IF NOT EXISTS session_agg (
  commodity     TEXT NOT NULL,
  session_date  TEXT NOT NULL,          -- 'YYYY-MM-DD'
  ice_code      TEXT NOT NULL,          -- 'CTZ6'
  generic_code  TEXT,                   -- as-of session_date, NULL out-of-universe
  window_preset TEXT NOT NULL,          -- night | day | full | other
  primary_type  TEXT NOT NULL,
  sum_size      DOUBLE PRECISION NOT NULL,
  trade_count   INTEGER NOT NULL,
  PRIMARY KEY (commodity, session_date, ice_code, window_preset, primary_type)
);
CREATE INDEX IF NOT EXISTS ix_sessagg_cmd_ice ON session_agg (commodity, ice_code, session_date);
```

- `full` rows are stored explicitly (= night + day; `other` excluded, matching
  `ingest/rollup.py` semantics) so no query-time addition is needed.
- Portable-SQL only, semicolon-comment rule respected (`db.init_schema` splits on `;`).
- Built by a new `ingest/session_aggregator.py::rebuild_session_agg(db, cmd, date)` from
  `ticks` (window_preset already tagged per tick), hooked into `ingest_day()` immediately
  after `rebuild_minute_agg` and therefore into backfill for free.

**Naming/params follow the existing API:** `preset=night|day|full`, `contracts=` (ice or
generic, `all`), `types=`, freshness envelope on everything, cache tags `sv:{cmd}:{date}`
per date touched.

**Timestamps stay naive-ET TEXT.** No new timezone machinery (MEMORY.md decision #4).

---

## 3. C1 — Multi-day continuous chart

### 3.1 Data model
No new table beyond `session_agg` (§2). Fine-grain buckets come from `minute_agg` —
`ix_minute_cmd_time` already serves `minute_ts BETWEEN a AND b` across days.

**Cost control (the hot-path rule):** cap rows per request, not features:

| bucket | max range allowed | worst-case minute_agg rows scanned (CT) |
|---|---|---|
| 1m | 10 sessions | ~10 × 1,000 × types ≈ 60k |
| 5m / 15m | 45 sessions | ~45k folded to ≤ 4k points |
| 60m / session | 250 sessions | served from `session_agg`, ≤ 3k rows |

Requests exceeding the cap → 400 with a "coarsen the bucket" message. Folding stays in
Python (portable), reusing `repository.profile`'s fold loop generalized to multi-day.

### 3.2 Repository additions (`store/repository.py`)
- `multi_profile(db, cmd, dates, preset, bucket_minutes, contracts, types)` — one SQL
  range scan per contiguous date-span over `minute_ts` (not per-day queries), then split
  results by session using per-date `window_bounds`; returns
  `[{session_date, buckets: [{bucket_ts, sum_size, trade_count}]}]`.
- `session_series(db, cmd, dates, preset, contracts, types)` — reads `session_agg` for
  bucket = whole-session points.

### 3.3 API — new endpoint
`GET /v1/sessionvol/{cmd}/multiday`

| param | values | notes |
|---|---|---|
| `from`, `to` | YYYY-MM-DD | required; resolved via `available_dates` (holidays/no-blotter days simply absent) |
| `preset` | `night\|day\|full` (default `full`) | per-session window |
| `bucket` | `1m\|5m\|15m\|60m\|session` | `session` served from `session_agg` |
| `contracts` | `CTZ6`, `CTDEC1`, `all` | same resolution as `/window` |
| `types` | csv of primary types | same as `/window` |

Response: `{commodity, from, to, preset, bucket, sessions: [{session_date, window:{start,end}, buckets:[...], splice: {generic, from_ice, to_ice} | null}], freshness}`.
`splice` is emitted on the first session where a requested **generic** resolves to a
different ice_code than the prior session (roll marker — see §9 R-1).

### 3.4 UI (`ui/templates/dashboard.html`)
New "Multi-day" tab. Plotly single trace (or one per type when types split), **x-axis =
bucket index (category axis)** with session-date tick labels at each session boundary —
this collapses the 14:20→21:00 dead zone and weekends instead of drawing flat gaps.
Vertical navy separators at session starts; gold splice markers on generic roll. Existing
freshness stamp reused.

---

## 4. C2 — Seasonal chart

### 4.1 Alignment key — recommendation
**Recommend: `days_to_roll` (dtr) = ICE-trading-days until the 1st of the delivery month**,
computed from `resolve_generic`'s existing roll anchor (`date(delivery_year, month, 1)`)
minus weekends and `config.CLOSED_DATES`. dtr decreases toward 0 at roll; negative after.

Why over the alternatives:
- **Calendar-week (rejected as primary):** a Dec contract's volume lifecycle is dominated
  by expiry-relative behavior (index roll, FND run-up); calendar weeks drift up to ±4
  trading days across years and smear exactly the spikes Lou wants to compare. Kept as a
  secondary `align=calweek` option (ISO week of session_date) since it's free to compute.
- **Days-to-first-notice / days-to-last-trade (deferred):** FND/LTD dates exist nowhere in
  `contract_resolver.py` — the ecosystem's only expiry anchor is delivery-month-start.
  Introducing an FND calendar means new shared reference data that the VLM repo would also
  want → blast radius. dtr is a constant offset from days-to-FND for CT (FND ≈ 5 business
  days before delivery month), so dtr preserves cross-year alignment fidelity for CT.
  Adding a true FND calendar later is **additive** (new column `days_to_fnd`), flagged as
  decision D-3.

### 4.2 Data model — deep history table
Tick tape covers ~45 days; seasonal needs years of **daily full-session per-contract
volume** from Bloomberg (`CTZ6 Comdty` `PX_VOLUME` daily history per delivery contract).

```sql
-- Daily per-contract volume, multi-source. Deep history (bloomberg) + tape-derived
-- rows coexist, disambiguated by source. contract_key carries the FULL delivery year
-- because single-digit ice codes collide across decades (CTZ6 = 2016 and 2026).
CREATE TABLE IF NOT EXISTS daily_contract_volume (
  commodity     TEXT NOT NULL,
  contract_key  TEXT NOT NULL,          -- 'CTZ2026' (prefix + month letter + 4-digit year)
  ice_code      TEXT NOT NULL,          -- 'CTZ6' display form
  session_date  TEXT NOT NULL,
  full_volume   DOUBLE PRECISION,
  night_volume  DOUBLE PRECISION,       -- NULL for source=bloomberg (daily-only)
  day_volume    DOUBLE PRECISION,       -- NULL for source=bloomberg
  days_to_roll  INTEGER,                -- trading days to 1st of delivery month
  source        TEXT NOT NULL,          -- 'tape' | 'bloomberg'
  generated_at  TEXT,
  PRIMARY KEY (commodity, contract_key, session_date, source)
);
CREATE INDEX IF NOT EXISTS ix_dcv_seasonal ON daily_contract_volume (commodity, contract_key, days_to_roll);
```

**Sourcing protocol (no new live integrations):** Lou exports per-contract daily volume
from the Terminal (HP export, one CSV per contract or one combined CSV:
`date,contract,volume`); a new one-shot loader `jobs/load_deep_history.py --file <csv>
--commodity CT` parses, computes `contract_key` + `days_to_roll`, upserts with
`source='bloomberg'`. The engine never calls Bloomberg. Files live under `data/deep_history/`
(this repo — never `C:\Ice eod records\`).

**Tape-side rows** are emitted by the same daily hook that builds `session_agg`: one
`source='tape'` row per traded contract per day (full/night/day from `session_agg`).
Where both sources exist for a date, **tape wins for intraday splits, bloomberg is the
cross-check** — the seasonal query reads `source='bloomberg'` for depth and overlays
`source='tape'` for the recent window; it never sums the two.

### 4.3 API — new endpoint
`GET /v1/sessionvol/{cmd}/seasonal`

| param | values | notes |
|---|---|---|
| `month` | `Z`, `H`, `K`, `N`, ... | validated against `commodity_meta.active_months(cmd)` — SB rejects `Z` |
| `years` | csv of 4-digit delivery years, or `last=5` | which contract vintages to overlay |
| `align` | `dtr` (default) \| `calweek` | §4.1 |
| `measure` | `full` (default) \| `night` \| `day` | night/day only where tape rows exist |
| `smooth` | int, optional | trailing N-day mean per series (UI convenience) |

Response: `{commodity, month, align, series: [{delivery_year, contract_key, points: [{x, session_date, volume, source}]}], today_marker: {x, volume} | null, freshness}`.
`today_marker` places the current front contract's latest session on the same x-axis —
this is the "is today's Dec volume high for this point in the season" answer.

### 4.4 UI
New "Seasonal" tab: month picker (from `commodity_meta.month_picker`), year multi-select,
align toggle. Plotly overlay — one muted-navy line per historical vintage, current vintage
in gold, `today_marker` as a gold diamond. X-axis reversed when `align=dtr` (high dtr →
0 left-to-right, so "toward expiry" reads rightward).

---

## 5. C3 — Anomaly flagging (replaces the reconcile-label UI column)

### 5.1 Statistic — recommendation
Compute **both**, display RVOL as primary:
- **RVOL ratio** `volume / trailing-N-mean` — identical semantics to
  `futures_session_volume.compute_rvol`; the number Lou already reads daily.
- **z-score** `(volume - trailing-N-mean) / trailing-N-sd` (population sd of the trailing
  window) — the "N-sigma" phrasing; gated harder because sd is unstable when thin.

Trailing window = **prior sessions only** (current session excluded), most-recent-first,
restricted to sessions **on/after the contract's first traded date** (zeros before a
contract exists are not observations; zero-volume trading days after it exists ARE
included — a dead day is signal).

### 5.2 Grain
`scope_kind × scope × window_preset`, all precomputed:

| scope_kind | scope values | trailing history is |
|---|---|---|
| `ice` | each traded ice_code | the contract's own life — **the primary "vs its OWN history" signal** |
| `generic` | in-universe slots (CTDEC1, ... pos 1–2) | the slot's history (splices across rolls — flagged, see §9 R-2) |
| `aggregate` | `ALL` | whole-commodity session total |

× `window_preset ∈ (night, day, full)`. Volume statistic = **all-in total** (sum of all
primary_types, consistent with `full_total` decision #1 in MEMORY.md); a `clean` variant
(excl. `efs_delete`) is a possible later additive column, not in v1.

### 5.3 Data model

```sql
-- Per-session anomaly stats, long format: one row per scope x window x tier.
-- Rebuilt for a session_date by jobs step 6.5; ~ (contracts+generics+1) x 3 x 5 rows/day.
CREATE TABLE IF NOT EXISTS session_stats (
  commodity     TEXT NOT NULL,
  session_date  TEXT NOT NULL,
  scope_kind    TEXT NOT NULL,          -- ice | generic | aggregate
  scope         TEXT NOT NULL,          -- 'CTZ6' | 'CTDEC1' | 'ALL'
  window_preset TEXT NOT NULL,          -- night | day | full
  tier          INTEGER NOT NULL,       -- 5 | 10 | 20 | 30 | 60 (from LOOKBACK_TIERS)
  volume        DOUBLE PRECISION NOT NULL,
  trail_avg     DOUBLE PRECISION,       -- NULL when n=0
  trail_sd      DOUBLE PRECISION,       -- NULL when n < Z_MIN_SESSIONS
  rvol          DOUBLE PRECISION,       -- volume / trail_avg
  zscore        DOUBLE PRECISION,       -- NULL when trail_sd NULL or 0
  n_sessions    INTEGER NOT NULL,       -- actual trailing count available
  flag          TEXT,                   -- high | low | NULL (thresholds in config)
  generated_at  TEXT,
  PRIMARY KEY (commodity, session_date, scope_kind, scope, window_preset, tier)
);
CREATE INDEX IF NOT EXISTS ix_sstats_flag ON session_stats (commodity, session_date, flag);
```

### 5.4 Thresholds + degradation (config additions, this repo's `config.py`)
```python
ANOMALY_RVOL_HIGH = 2.0      # mirrors _rvol_line flag levels in futures_session_volume.py
ANOMALY_RVOL_LOW  = 0.5
ANOMALY_Z_HIGH    = 2.0      # |z| >= 2 corroborates; z alone never flags
RVOL_MIN_SESSIONS = 5        # below this: stats emitted with note-style NULL flag, never flagged
Z_MIN_SESSIONS    = 20       # sd/zscore NULL below this (sd meaningless thin)
```
Degradation **mirrors `compute_rvol` exactly**: `n < tier` still writes the row with the
partial `trail_avg`/`rvol` and true `n_sessions` (the API exposes `n` so the UI can grey
it) — but `flag` is only set when `n_sessions >= RVOL_MIN_SESSIONS` for that tier's row,
and z-based corroboration only when `n_sessions >= Z_MIN_SESSIONS`. Until the 45-day
backfill, only the 5/10/20/30 tiers fill; tier-60 populates with `n<60` notes for months —
by design, never an error.

### 5.5 Compute + hooks
New `ingest/stats.py::rebuild_session_stats(db, cmd, session_date)`:
1. Read the day's totals from `session_agg` (ice + aggregate) and generic mapping.
2. Pull trailing volumes per scope from `session_agg` (`session_date < today ORDER BY
   session_date DESC LIMIT 60`), one query per scope_kind (grouped), not per scope.
3. Vendor the tier loop with the same shape as `compute_rvol` (import it if importable
   cleanly from the VLM repo path — it is, via the existing `sys.path` append — decision
   D-2 leans **import**, keeping one implementation).
4. Delete+reinsert the day's rows (aggregator pattern), `db.commit()`.

Hook: `jobs/daily_ingest.py::ingest_day` step 6.5 (after rollup, before CF purge — purge
already covers the day tag). Backfill inherits it; ascending date order makes trailing
windows correct. A `jobs/rebuild_stats.py --commodity CT --from --to` utility recomputes
stats without re-ingesting ticks (needed after threshold changes).

### 5.6 API
- `GET /v1/sessionvol/{cmd}/anomalies?date=&window=full&tier=20&scope_kind=ice`
  → `{date, tier, rows: [{scope_kind, scope, volume, trail_avg, trail_sd, rvol, zscore, n_sessions, flag}], thresholds: {...}, freshness}` — full stats rows, flagged first.
- `GET /v1/sessionvol/{cmd}/stats?scope=CTZ6&window=full&from=&to=&tier=20`
  → time series of one scope's stats (feeds a sparkline / the multi-day chart's anomaly shading).
- `/catalog` gains `"anomaly_tiers": [5,10,20,30,60]` and the threshold block (additive).

### 5.7 UI
- Main table: new **RVOL column** where the reconcile label used to sit — badge
  `2.4x (20)` gold-filled when `flag=high`, muted when `low`, grey `n/a (3 of 20)` when thin.
  Tier selector (default 20) in the toolbar.
- Multi-day chart (C1): sessions with `flag=high` for the selected scope get a translucent
  gold band — the two features compose.

---

## 6. Computable now vs needs-deep-backfill

| Capability | Now (2 sessions) | After Mon 45-day backfill | Needs Bloomberg deep history |
|---|---|---|---|
| C1 multi-day chart (any bucket) | ✅ 2-session strip (mechanics provable) | ✅ full intended use | — |
| C1 generic splice markers | ✅ (07-01→07-02 N7 roll is a live test case) | ✅ | — |
| C3 stats rows + rvol, n-noted | ✅ (n=1, everything `n/a`-noted) | ✅ tiers 5–30 trustworthy; tier-60 fills over time | — |
| C3 z-score / flags | ❌ (below RVOL_MIN_SESSIONS) | ✅ z after ~20 sessions in-tape | — |
| C2 intraday-split seasonal (night/day) | ❌ | ~45 days of `source='tape'` only | ✅ full-session daily depth per vintage |
| C2 multi-year Dec-vs-Dec overlay | ❌ | ❌ (one partial vintage) | ✅ **hard-gated on Bloomberg export** |
| C2 dtr alignment machinery | ✅ (pure function of resolver + holiday set — unit-testable today) | ✅ | — |

---

## 7. Build sequence

| # | Step | Gate |
|---|---|---|
| 1 | `session_agg` DDL + `ingest/session_aggregator.py` + hook in `ingest_day` + tests | none — additive, buildable now |
| 2 | C1: `multi_profile`/`session_series` repo fns + `/multiday` endpoint + tests against the 07-01/07-02 fixture pair (incl. the N7 splice) | step 1 |
| 3 | **Run 45-day backfill (Lou, Monday)**; eyeball C1 over the full strip; confirm a few clean daily-ingest sessions | external |
| 4 | C3: `session_stats` DDL + `ingest/stats.py` + `jobs/rebuild_stats.py` + `/anomalies` + `/stats` + UI RVOL column | step 3 (needs real depth to sanity-check flags before Lou trusts them) |
| 5 | C1 UI tab (continuous chart + anomaly shading) | steps 2 + 4 |
| 6 | C2: `daily_contract_volume` DDL + dtr calculator + `jobs/load_deep_history.py`; **Lou exports Bloomberg per-contract daily volume** (recommend: Dec + Jul vintages 2019–2026 first) | Lou's export; dtr calc buildable at step 1 |
| 7 | C2 `/seasonal` endpoint + Seasonal UI tab | step 6 |

Each step ships with its own tests green before the next starts; nothing deploys to
Supabase/Railway until the phase-1 deploy decision (MEMORY.md) is made anyway.

## 8. Acceptance criteria (per feature, `test_acceptance_0702` style)

**C1** — off the locked 07-01+07-02 real data:
- `/multiday?from=2026-07-01&to=2026-07-02&preset=full&contracts=CTZ6&bucket=60m`:
  session 07-02 buckets **byte-match** the existing single-day `/profile` 60m output
  (21:00→520, 22:00→157, …), and 07-02 full total = **18,366**.
- `bucket=session` totals equal the rollup sidecar CSV's night/day/full for both days
  (07-01 full = 27,003 aggregate).
- `contracts=CTJUL1` across 07-01→07-02 returns a `splice` marker (N7 enters the slot on
  07-02); `contracts=CTN7` returns both days, no marker.
- Aggregate (`all`) equals the sum of per-contract requests for the same range (no double count).
- 1m bucket over >10 sessions → 400.

**C2**
- dtr unit tests: for CTZ2026, dtr(2026-11-30)=1 trading day if 12-01 is a trading day;
  dtr skips weekends and every date in `CT_CLOSED_DATES`; dtr(first-of-delivery-month)=0;
  negative after.
- Load a hand-built 3-vintage fixture CSV → `/seasonal?month=Z&years=...&align=dtr`
  returns 3 series whose point at x=K matches the fixture row hand-computed for that dtr.
- `month=Z` on SB → 400 (month set from `commodity_meta`).
- Overlap day (tape + bloomberg both present) appears **once** per series, `source` correctly attributed.

**C3**
- Synthetic fixture: 25 sessions of volume 100 then one of 250 → tier-20 row has
  `trail_avg=100, rvol=2.5, zscore` computed on the exact trailing 20, `flag='high'`;
  tier-60 row has `n_sessions=25`, no flag suppression error.
- n=3 history → rvol emitted, `flag IS NULL`, `n_sessions=3` (mirrors `compute_rvol`
  note behavior); zscore NULL below `Z_MIN_SESSIONS`.
- Zero-volume trading day inside the window is included in the mean; pre-first-trade days excluded.
- Re-running `rebuild_session_stats` for the same day is idempotent (row counts stable).
- `ALL` aggregate volume for a date == sum of `ice` scope volumes for that date/window.

## 9. Risks / edge-case register

| # | Risk | Handling |
|---|---|---|
| R-1 | **Generic roll inside a C1 date range** (CTDEC1 = different ice codes across the range) | Filter matches per-session as today (`_contract_filter` OR); response carries `splice` markers; UI draws a gold roll line. Never silently splice without the marker |
| R-2 | **Generic-scope C3 trailing history splices across a roll** (front-month volume jumps at roll by construction) | Documented in the endpoint; `ice` scope is the primary signal; generic rows carry `scope_kind='generic'` so the UI can caption "slot history, spliced" |
| R-3 | Contract **expires mid-range** in C1/C2 (no rows after last trade) | Absent sessions are absent points, not zeros; C2 series simply end at dtr of last trade |
| R-4 | **Holidays / no-blotter days** in a continuous series | `available_dates` drives the session list — such days never appear as empty slots; category x-axis means no visual gap distortion |
| R-5 | **DST in naive ET** | Windows are ET wall-clock by design (MEMORY #4); night session is 10h/9h wall-clock across transitions — bucket **counts per session differ**; C1 must key on bucket_ts, never assume fixed buckets/session; C2 unaffected (daily grain) |
| R-6 | **Thin-history sigma instability** | `Z_MIN_SESSIONS=20` hard floor for sd/z; rvol-only below; flags additionally floored at `RVOL_MIN_SESSIONS`; `n_sessions` always surfaced |
| R-7 | **Aggregate vs per-contract double counting** | Aggregate rows computed from the same `session_agg` base and asserted equal to the per-ice sum in tests (§8 C3); `ice`/`generic`/`aggregate` never summed together in any endpoint |
| R-8 | **Single-digit ice-code year collision in deep history** (CTZ6 = 2016 & 2026) | `contract_key` with 4-digit year is the PK; loader derives it from the export's explicit contract naming, never from `parse_ice_code`-style inference on old dates |
| R-9 | Bloomberg daily volume definition vs tape all-in total (blocks/EFS inclusion may differ) | Overlap window (45 tape days vs bloomberg same days) is the calibration set; store both sources, never mix in one series; document observed basis in MEMORY.md after first load |
| R-10 | Multi-day queries stressing the hot path | Row-cap table (§3.1) + `session_agg` for coarse grains; no endpoint ever scans `ticks` for a range query |
| R-11 | Backfill ordering vs stats correctness | `jobs/backfill.py` is ascending (verified); `jobs/rebuild_stats.py` re-derives stats-only if a mid-history day is ever re-ingested |
| R-12 | tier-60 RVOL crossing a generic roll for `ice` scope near contract birth/death | `ice` scope trailing window starts at first-traded date (§5.1) — early-life flags suppressed by n-floors |

## 10. Blast radius + decisions needed from Lou

**Blast-radius inventory (everything phase 2 touches that is shared):**
- All new tables (`session_agg`, `daily_contract_volume`, `session_stats`) and endpoints
  (`/multiday`, `/seasonal`, `/anomalies`, `/stats`) are **additive** — no existing
  response shape, column, or sidecar CSV schema changes. `/catalog` gains keys (additive).
- **Nothing writes** to `C:\Ice eod records\` or to `VLM_Session_Volume_Project/data/history/*`.
- `compute_rvol` is **imported** from the VLM repo, read-only (D-2) — no change to that file.

**Decisions required before build:**

| ID | Decision | Recommendation |
|---|---|---|
| D-1 | ✅ **RESOLVED in commit `73eff25` (2026-07-04), before phase 2.** Blank prints now fold into a single `outright` primary_type (ask/bid/unstamped are an informational `outright_side` sub-split); reconcile-vs-settle removed from the trader UI. Phase 2 inherits the clean bucket set `outright, leg, efs, efp, block, efs_delete, other`. No further action. | Done — no decision needed |
| D-2 | Import `compute_rvol` from the VLM repo vs vendor a copy here | Import (single source of truth; path plumbing already exists in `config.py`) |
| D-3 | Seasonal alignment: dtr now, true FND/LTD calendar later? | Ship dtr; add `days_to_fnd` column + shared FND calendar only if cross-year dtr alignment proves insufficient — that calendar is shared reference data → its own blast-radius review |
| D-4 | C2 deep-history scope of first Bloomberg export (which months/vintages, how far back) | Dec + Jul, 2019–2026 (8 Dec vintages ≈ the seasonal question Lou actually asks) |
| D-5 | C3 flag thresholds (2.0×/0.5×, z≥2, floors 5/20) | Adopt as config defaults; tune after 20+ live sessions |
| D-6 | Does the anomaly flag ever roll up into the sidecar CSVs / shared VLM files? | No in phase 2 (sidecar schema untouched); revisit with the existing MEMORY.md item 4 gate |
