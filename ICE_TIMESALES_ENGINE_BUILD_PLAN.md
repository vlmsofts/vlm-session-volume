# ICE Time & Sales → Session-Volume Analytics Engine — Build Plan

**Scope:** daily-collected analytics engine over ICE futures tick tape (EOD blotter files), slicing traded volume by arbitrary time block × contract month × aggregate × trade type, ending in a queryable Flask/Plotly UI with near-zero latency. **CT first, softs-ready (KC/CC/SB).** Futures only — options files out of scope.

**Status of ground truth:** verified against `config.py`, `contract_resolver.py`, and both history CSV headers in `VLM_Session_Volume_Project` this session. Blotter schema, conditions vocabulary, and the 07-02 acceptance numbers are taken as given (user-verified). ICE data root `C:\Ice eod records\` is not mounted in this planning session — Fable must confirm folder paths at build time.

> ### HARD RULE — source is READ-ONLY
> `C:\Ice eod records\` (all commodities, all day-folders, every `.csv`) is the immutable source of truth. The engine **opens these files read-only and never writes, moves, renames, deletes, rewrites, or "cleans" anything under this tree** — the data is already complete and clean and stays in its on-disk form. A single day-folder (e.g. `C:\Ice eod records\CT\2026-07-02\`) is the complete input for that session. **Every derived artifact is written elsewhere:** Supabase Postgres, the engine repo's own sidecar CSVs, and `logs/`. `discover.py` and `blotter_parser.py` must open with read-only mode; any file handle pointed at the ICE root is read-only by construction. This is non-negotiable and overrides any convenience shortcut.

---

## 0. Reconciling two things in the spec before anyone writes code

Two points in the brief look contradictory and would send a coder in circles. Resolved here:

1. **"Exclude EFS/Delete from totals" vs. acceptance `full_total = 18,366`.** The by-condition breakdown sums *exactly* to 18,366 (6717+6008+4766+522+207+99+47), i.e. `full_total` is the **raw all-in tape sum including EFS and EFS-Delete**. So: **`full_total` stored in history = raw all-in** (reproduces acceptance, is the self-validating tape total). "Exclude EFS/Delete" is a **default UI view toggle**, not the definition of the stored total. We additionally compute `clean_total = full_total − efs_delete` as a convenience column, but the canonical number is all-in. Open decision resolved as **(c) selectable** (§9).

2. **"A print appears under multiple type-views" vs. the breakdown summing to the total.** Resolved by two distinct concepts (§3.3): each print gets **one mutually-exclusive `primary_type`** (this is what sums to the total and what `minute_agg` stores), assigned by a **precedence ladder** over its comma-tokenized tags. Multi-tag membership (e.g. `BlockTrde, Leg`) is used *only* to (a) pick the primary bucket via precedence and (b) drive informational "show all leg-involved / all block-involved prints" filters — those filtered views are **never summed together**, so no double count.

---

## 1. Module layout (new repo: `ice_timesales_engine`)

```
ice_timesales_engine/
├── config.py                     # NEW — extends session windows/paths; ICE root; commodity-parametric
├── commodity_meta.py             # NEW — per-commodity month-sets + month picker metadata
├── contract_resolver.py          # REUSE (import from VLM_Session_Volume_Project) — CT today; §8 softs note
├── ingest/
│   ├── discover.py               # locate day-folders + blotter files on disk
│   ├── blotter_parser.py         # read one blotter CSV → raw rows (typed)
│   ├── normalize.py              # "CT Z26"→"CTZ6", ET parse, session_date + window tagging
│   ├── classifier.py             # Conditions tokenizer → primary_type + tag flags (precedence)
│   ├── loader.py                 # idempotent upsert of ticks into Postgres
│   ├── aggregator.py             # (re)build minute_agg for a session_date
│   ├── spreads.py                # spreads_*.csv Block Volume → block_supplement (dedup vs tape)
│   ├── reconcile.py              # tape vs futures_settle_* delta → reconcile_flags
│   └── rollup.py                 # emit compat history rows (session + per-contract)
├── store/
│   ├── db.py                     # psycopg pool / Supabase connection
│   ├── schema.sql                # DDL (ticks, minute_agg, ingest_log, reconcile_flags, block_supplement)
│   └── repository.py             # query helpers (window sum, profile, contract list, freshness)
├── api/
│   ├── app.py                    # Flask app factory + blueprint registration
│   ├── routes_query.py           # /v1/sessionvol/* endpoints
│   ├── windows.py                # window resolution (presets + custom, ET, cross-midnight, [start,end))
│   └── cache.py                  # Cache-Control + Cloudflare cache-tag purge
├── ui/
│   ├── app.py                    # Flask UI (or blueprint on same app)
│   ├── templates/dashboard.html
│   └── static/                   # Plotly, JS controls
├── jobs/
│   ├── daily_ingest.py           # orchestrator (cron after EOD capture)
│   └── backfill.py               # one-shot ingest of all existing day-folders
├── tests/
│   ├── fixtures/…                # trimmed 07-02 CT Z26 blotter + settle + spreads
│   ├── test_normalize.py
│   ├── test_classifier.py
│   ├── test_windows.py
│   ├── test_idempotency.py
│   ├── test_missing_forward.py
│   └── test_acceptance_0702.py   # the locked numbers
├── requirements.txt
├── railway.toml / Dockerfile
├── MEMORY.md
└── ERRORS.md
```

**Reuse rule:** import `config` (windows, `CT_CLOSED_DATES`, month meta, history paths) and `contract_resolver` (`ice_to_generic`, `resolve_generic`, `parse_ice_code`, `build_capture_universe`) from `VLM_Session_Volume_Project` — never re-hardcode symbology. New `config.py` here holds only ICE-root paths, DB config, and commodity parametrization; it re-exports the reused constants.

---

## 2. Data contracts

### 2.1 Input — futures blotter (verified header)
`Contract,Exchange Time,Price,Size,Conditions,Seq Num`

| Field | Type | Notes |
|---|---|---|
| Contract | str | `"CT Z26"` — space + 2-digit year. Normalize → `CTZ6`. |
| Exchange Time | ISO str, no TZ | ET wall-clock, e.g. `2026-07-01T21:00:00`. Session spans 21:00 ET prior day → ~14:20 ET session date. |
| Price | float | **0.0 for EFP/EFS** — exclude from price-weighted metrics. |
| Size | float | The volume unit (lots). |
| Conditions | str | Compound, comma-joined. Tokenize; match by membership. |
| Seq Num | int | Exchange sequence id → idempotency / dedup key. |

Path: `<ICE_ROOT>\<COMMODITY>\<session_date>\futures_blotter_<COMMODITY>_<FWD>_<session_date>.csv`, `<FWD>` ∈ {Z26,H27,…}. **Missing file = zero volume, not a gap.**

### 2.2 Cross-check inputs (NOT ground truth)
- `futures_settle_<date>.csv` — settle block; stale carry-forward rows; reconcile only.
- `spreads_<date>.csv` — calendar spreads incl. `Block Volume`; feeds `block_supplement`.

### 2.3 Normalized tick (internal + `ticks` row)
`commodity, session_date, ice_code, generic_code|None, exchange_time (naive ET), price, size, primary_type, conditions_raw, seq_num, window_preset (night|day|other)`.

---

## 3. Ingest — functions & signatures

### 3.1 `discover.py`
```python
def find_day_folders(ice_root: str, commodity: str,
                     date_from: date|None=None, date_to: date|None=None) -> list[date]
def find_blotter_files(ice_root: str, commodity: str, session_date: date) -> list[Path]
def parse_fwd_from_filename(path: Path) -> str          # -> "Z26"
```
Missing folder / no blotters → returns empty list (caller logs zero, never errors).

### 3.2 `blotter_parser.py`
```python
@dataclass
class RawTick:
    contract: str; exchange_time: str; price: float; size: float
    conditions: str; seq_num: int
def read_blotter(path: Path) -> Iterator[RawTick]     # streaming; tolerant of blank Conditions
def file_sha256(path: Path) -> str                    # for ingest_log incremental skip
```
Guards: coerce `Size` to float; empty `Conditions` → `""` (not NaN); skip fully blank lines.

### 3.3 `classifier.py` — the trade-type engine
```python
TAGS = {"SetByAsk","SetByBid","Leg","EFS","EFP","BlockTrde","Delete"}

def tokenize(conditions: str) -> frozenset[str]       # split on ",", strip, drop "" -> members
def tag_flags(tokens: frozenset[str]) -> dict[str,bool]
def primary_type(tokens: frozenset[str]) -> str       # mutually-exclusive bucket
```
**Precedence ladder (first match wins) — sums to total, reproduces acceptance:**
1. `efs_delete` — `EFS` **and** `Delete` present
2. `efp` — contains `EFP`
3. `efs` — contains `EFS` (no Delete)
4. `block` — contains `BlockTrde`
5. `leg` — contains `Leg`
6. `outright_ask` — contains `SetByAsk`
7. `outright_bid` — contains `SetByBid`
8. `blank` — empty token set
9. `other` — safety catch-all (log if ever hit)

`tag_flags` (independent booleans `is_leg`, `is_block`, …) support "all leg-involved" style filter views only; never summed with primary buckets. `outright = outright_ask + outright_bid` is a derived display grouping.

### 3.4 `normalize.py`
```python
def normalize_contract(raw: str) -> str               # "CT Z26" -> "CTZ6" (strip space, year->last digit)
def parse_et(ts: str) -> datetime                     # naive ET datetime
def assign_window(exchange_time: datetime, session_date: date) -> str   # night|day|other
def to_generic(ice_code: str, session_date: date, commodity: str) -> str|None  # via contract_resolver
```
`assign_window` uses `config` windows: night = `[session_date-1 21:00, session_date 07:00)`, day = `[session_date 07:00, session_date 14:20)`, else `other`. Half-open — a `07:00:00` tick is **day**.

### 3.5 `loader.py`
```python
def upsert_ticks(conn, rows: Iterable[NormTick]) -> int   # ON CONFLICT DO NOTHING; returns inserted
def record_ingest(conn, meta: IngestMeta) -> None
def already_ingested(conn, commodity, session_date, ice_code, sha256) -> bool
```
Idempotency key: `(commodity, session_date, ice_code, seq_num)`. Skip file whole if `sha256` unchanged.

### 3.6 `aggregator.py`
```python
def rebuild_minute_agg(conn, commodity: str, session_date: date) -> int
```
Delete + reinsert `minute_agg` rows for that `(commodity, session_date)` from `ticks`, grouped `(ice_code, date_trunc('minute', exchange_time), primary_type)` → `sum(size), count(*)`. Safe to re-run (day is the unit).

### 3.7 `spreads.py`
```python
def ingest_block_volume(conn, commodity, session_date, spreads_path) -> None
```
Load `Block Volume` into `block_supplement`. **Do not add to tape total by default** (avoids double count with tape `BlockTrde`). Dedup candidate blocks by `(ice_code, price, size, minute)`. Surfaced separately; opt-in include of net-new blocks only.

### 3.8 `reconcile.py`
```python
def build_reconcile(conn, commodity, session_date, settle_path) -> None
```
`tape_total` (all-in per contract) vs `settle_volume`; `delta = settle − tape`; label `expected_gap` (implied/spread-matched/block fills never printed as outright ticks — expected, per 07-02 Z26: 6,709 spread legs executed vs 4,766 leg-ticks) vs `suspect_capture` (delta outside plausible band, e.g. tape ≫ settle). **Surface, never enforce.**

### 3.9 `rollup.py`
```python
def emit_session_rows(conn, commodity, session_date) -> dict       # session-level compat row
def emit_contract_rows(conn, commodity, session_date) -> list[dict]# per-contract compat rows
```
Schemas match existing history **exactly** (confirmed this session):
- session: `date,commodity,night_total,day_total,full_total,night_share,night_day_ratio,source,split_source,generated_at` — `source='ice_blotter_tape'`, `split_source='ice_blotter'`.
- per-contract: `date,commodity,generic_code,ice_code,month_code,month_name,delivery_year,position,night,day,full,generated_at`.
- Emit a per-contract row for **every traded `ice_code`**; `generic_code` populated only for in-universe (pos 1–2 of active months) else **blank** (out-of-universe still trades, e.g. far Dec / Oct).
- `night/day/full = all-in` sums (reproduces acceptance). ⚠️ **Blast-radius stop (§7).**

---

## 4. Postgres schema (`store/schema.sql`)

```sql
-- COLD: every tick, permanent. Monthly range-partition on session_date.
CREATE TABLE ticks (
  id             BIGSERIAL PRIMARY KEY,
  commodity      TEXT NOT NULL,
  session_date   DATE NOT NULL,
  ice_code       TEXT NOT NULL,
  generic_code   TEXT,
  exchange_time  TIMESTAMP NOT NULL,          -- naive ET
  price          DOUBLE PRECISION,
  size           DOUBLE PRECISION NOT NULL,
  primary_type   TEXT NOT NULL,
  conditions_raw TEXT,
  seq_num        BIGINT NOT NULL,
  window_preset  TEXT,                         -- night|day|other
  ingested_at    TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT uq_tick UNIQUE (commodity, session_date, ice_code, seq_num)
);
CREATE INDEX ix_ticks_cmd_time    ON ticks (commodity, exchange_time);
CREATE INDEX ix_ticks_cmd_date_c  ON ticks (commodity, session_date, ice_code);

-- HOT: pre-aggregated 1-min buckets per contract per primary_type.
CREATE TABLE minute_agg (
  commodity    TEXT NOT NULL,
  session_date DATE NOT NULL,
  ice_code     TEXT NOT NULL,
  generic_code TEXT,
  minute_ts    TIMESTAMP NOT NULL,            -- naive ET, truncated to minute
  primary_type TEXT NOT NULL,
  sum_size     DOUBLE PRECISION NOT NULL,
  trade_count  INTEGER NOT NULL,
  PRIMARY KEY (commodity, session_date, ice_code, minute_ts, primary_type)
);
CREATE INDEX ix_minute_cmd_time ON minute_agg (commodity, minute_ts);
CREATE INDEX ix_minute_cmd_date ON minute_agg (commodity, session_date, ice_code);

CREATE TABLE ingest_log (
  commodity TEXT, session_date DATE, ice_code TEXT, file_name TEXT,
  file_sha256 TEXT, rows_read INT, rows_inserted INT,
  status TEXT,                                -- ok|skipped|empty|error
  ingested_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (commodity, session_date, ice_code, file_name)
);

CREATE TABLE reconcile_flags (
  commodity TEXT, session_date DATE, ice_code TEXT,
  tape_total DOUBLE PRECISION, settle_volume DOUBLE PRECISION,
  delta DOUBLE PRECISION, delta_pct DOUBLE PRECISION,
  label TEXT, generated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (commodity, session_date, ice_code)
);

CREATE TABLE block_supplement (
  commodity TEXT, session_date DATE, ice_code TEXT,
  block_volume DOUBLE PRECISION, source TEXT,   -- 'spreads'|'tape'
  on_tape BOOLEAN, generated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (commodity, session_date, ice_code, source)
);
```
**Why Postgres not SQLite:** UI is hosted + remote; concurrent reads; edge in front. **Naive ET storage** (not timestamptz): windows are defined in ET wall-clock and data is ET wall-clock, so naive comparison is exact and DST-safe for this use.

---

## 5. Query API (`api/`)

Base `/v1/sessionvol`. All reads; Cloudflare-fronted.

| Endpoint | Key params | Returns |
|---|---|---|
| `GET /{cmd}/window` | `date` or `from`+`to`; `preset`(night\|day\|full) or `start`+`end`(HH:MM ET); `contracts`(csv ice_code \| generic \| `all`); `types`(csv include) or `exclude`; `groupby`(contract\|type\|month\|none) | totals + breakdowns + freshness + reconcile |
| `GET /{cmd}/profile` | `date`, `bucket`(1m\|5m\|15m\|60m), `contracts`, `types` | array `{bucket_ts, sum_size, by_type?}` for charts |
| `GET /{cmd}/contracts` | `date` | traded ice_codes + generic mapping (UI picker) |
| `GET /{cmd}/reconcile` | `date` | reconcile_flags rows |
| `GET /catalog` | — | commodities, month-sets, presets, available dates, freshness |

**Window resolution (`windows.py`):** presets map to concrete ET timestamps per `session_date` from `config`. Custom `[start,end)` summed from `minute_agg`:
```sql
SELECT primary_type, SUM(sum_size) s, SUM(trade_count) n
FROM minute_agg
WHERE commodity=%s AND minute_ts >= %s AND minute_ts < %s
  AND (%s IS NULL OR ice_code = ANY(%s))
  AND primary_type = ANY(%s)
GROUP BY primary_type;
```
`all`-aggregate = omit the `ice_code` filter. Sub-minute / non-minute-aligned windows fall back to a `ticks` scan (`repository.window_from_ticks`) — rare, acceptable.

**Response shape (`/window`):**
```json
{ "commodity":"CT", "dates":["2026-07-02"],
  "window":{"preset":"full","start":"2026-07-01T21:00","end":"2026-07-02T14:20"},
  "totals":{"all":18366.0,"clean":18267.0,
            "by_type":{"outright_ask":6717,"outright_bid":6008,"leg":4766,"blank":522,"efs":207,"efs_delete":99,"efp":47},
            "by_contract":{"CTZ6":18366.0}},
  "freshness":{"generated_at":"…","source":"ice_blotter","stale":false,"stale_age_seconds":0},
  "reconcile":[{"ice_code":"CTZ6","tape_total":18366,"settle_volume":…,"delta":…,"label":"expected_gap"}] }
```
Freshness envelope mirrors the VLM gateway convention (`cached`/`stale`/`stale_age_seconds`) — UI must surface it.

**Caching (`cache.py`):** cache key = `(cmd, endpoint, from, to, preset|start|end, sorted(contracts), sorted(types), bucket, groupby)`. Tag each response `Cache-Tag: sv:{cmd}:{session_date}`. Past/closed sessions → long TTL immutable; latest session → short TTL. Daily collector is **sole writer** → after ingest, purge tag `sv:{cmd}:{session_date}` via Cloudflare purge API (event-driven, clean).

**Latency budget:** edge hit → ms; custom minute-sum → <100 ms; sub-minute tick scan → rare.

---

## 6. Daily job & backfill (`jobs/`)

`daily_ingest.py(commodity, session_date=latest)`:
1. If `session_date ∈ CT_CLOSED_DATES` → log holiday, exit 0.
2. `find_blotter_files`; none → log `no_blotter=zero`, exit 0 (not failure).
3. Per file: sha256 vs `ingest_log`; unchanged → skip. Else parse → normalize → classify → `upsert_ticks` → `record_ingest`.
4. `rebuild_minute_agg(commodity, session_date)`.
5. `spreads.ingest_block_volume`; `reconcile.build_reconcile`.
6. `rollup.emit_*` → append compat history (dedup by key). ⚠️ **§7 gate.**
7. Cloudflare purge `sv:{cmd}:{session_date}`.
8. Structured log to `logs/` + `ingest_log.status`.

`backfill.py(commodity, --from, --to)`: iterate existing day-folders, same per-day pipeline, idempotent. On-disk now: `2026-04-27, 05-01, 06-25, 06-26, 06-29, 06-30, 07-01, 07-02` (07-03 holiday). Deeper history via re-running capture with `--date` (ICE retains ~45 sessions); engine works off whatever folders exist.

---

## 7. Blast-radius gate (STOP before writing shared files)

Per CLAUDE.md rule #1. The existing `data/history/futures_session_volume_history*.csv` are consumed by the current session-volume engine/dashboard. The brief says "additive, `split_source='ice_blotter'`," but ice_blotter rows include **blank `generic_code`** (out-of-universe contracts) — new-shaped data in a shared file.

**Recommended default: write ice_blotter rollup to SEPARATE sidecar files** with identical schema — `futures_session_volume_history_ICE.csv` / `…_by_contract_ICE.csv` — so the protected shared contract is untouched. **Do not append into the shared files until Lou explicitly approves.** (Options_flow_analyzer O.I. pipeline is a different tree — no collision; state it and move on.)

---

## 8. CT-first, softs-ready

`commodity_meta.py`:
```python
COMMODITY_MONTHS = {
  "CT": frozenset("HKNZ"),    # Oct(V)/Aug(Q) excluded
  "KC": frozenset("HKNUZ"),   # V serial excluded
  "CC": frozenset("HKNUZ"),
  "SB": frozenset("HKNV"),    # NO December; V bona-fide
}
```
Month picker + aggregation read from this map (esp. **SB has no Dec** — aggregation must not expect one). `contract_resolver` is CT-only today (`_CT_ACTIVE`); softs need a **small parametric extension**: `_ACTIVE[commodity]` keyed by `COMMODITY_MONTHS`. CT build imports the resolver as-is; softs is a follow-on resolver change (flag, don't block CT).

---

## 9. Open decision — default "session volume" number

**Recommend (c) selectable.** Store all-in `full_total` (locks acceptance); expose the full by-type breakdown; UI toggles include/exclude `outright/leg/efs/efp/block` (+ separate `exclude deletes`). Nothing hidden, headline configurable, canonical stored total is all-in.

---

## 10. Edge-case register

1. `"CT Z26"→"CTZ6"` normalization before resolver. 2. Oct(V)/Aug(Q) CT → `generic_code=None`, still ingest tape. 3. Missing forward file = zero, not gap. 4. `EFS` bucket; `EFS,Delete` separate; delete surfaced. 5. Blank `""` → own bucket, never folded into outright. 6. EFP/EFS `Price=0.0` → excluded from price-weighted metrics; Size counts. 7. `[start,end)` half-open; `end` tick excluded; `07:00:00`→day. 8. Cross-midnight overnight via `exchange_time` + `session_date` folder tag. 9. Pre-07:00 on session_date = night. 10. Compound conditions: comma-tokenize, membership match, precedence for primary. 11. Multi-tag counted once in total; shown in filter views. 12. **SB no-Dec** parametric. 13. Seq-Num idempotency `(cmd, session_date, ice_code, seq_num)`. 14. Settle = cross-check only (stale carry-forward). 15. Block double-count: tape vs spreads separate; tape=truth; dedup. 16. Holiday skip (`2026-07-03` verified). 17. DST: naive ET storage. 18. ICE single-digit year → 4-digit via resolver `as_of=session_date`. 19. File re-run/rewrite → sha256 in `ingest_log`. 20. Reconcile band labels expected vs suspect.

---

## 11. Acceptance test (`test_acceptance_0702.py`) — must reproduce

Load 07-02 CT Z26 blotter fixture, config windows, `[start,end)` tape sums:

| Check | Expected |
|---|---|
| night | **3,667** |
| day | **14,699** |
| full | **18,366** |
| SetByAsk | 6,717 |
| SetByBid | 6,008 |
| Leg | 4,766 |
| blank | 522 |
| EFS | 207 |
| EFS-Delete | 99 |
| EFP | 47 |
| outright (ask+bid) | 12,725 |

By-condition sum = 18,366 = night + day (self-validating). Reconcile note: spreads executed 6,709 Z26 legs vs 4,766 leg-ticks printed → `expected_gap`, not loss.

Additional unit tests: `test_normalize` (`CT Z26→CTZ6`, `CT V26→CTV6` generic `None`); `test_classifier` (`BlockTrde, Leg`→primary `block`; `EFS,Delete`→`efs_delete`); `test_windows` (07:00 boundary→day, pre-0700→night, cross-midnight); `test_idempotency` (re-ingest → 0 new rows); `test_missing_forward` (absent file → zero, no error).

---

## 12. Build order for Fable (one pass)

1. `config.py` + `commodity_meta.py` (reuse imports). 2. `normalize.py` + `classifier.py` + **their unit tests** (cheapest correctness win). 3. `store/schema.sql` + `db.py`. 4. `blotter_parser.py` → `loader.py` → `aggregator.py`. 5. `test_acceptance_0702.py` green off the fixture. 6. `reconcile.py` + `spreads.py`. 7. `rollup.py` → **sidecar files** (§7). 8. `jobs/backfill.py` over on-disk folders; then `daily_ingest.py`. 9. `repository.py` + `api/` + `windows.py` + `cache.py`. 10. `ui/`. 11. Cloudflare cache-tags + Railway deploy. Deploy gating per CLAUDE.md: local tests auto-run; non-breaking auto-deploy; shared-contract writes (§7) stop for Lou.
