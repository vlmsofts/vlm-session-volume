# VLM Data Gateway — API Reference

**Base URL:** `https://vlmapi.vlmdata.com`  
**Auth:** `X-VLM-API-Key` header required on every request. Returns `401` if missing or wrong.  
**Version:** v1  
**Service:** Read-only REST API. All methods are GET. No writes, no mutations.

---

## Setup (all examples assume these)

```python
import requests

API_KEY = "your-api-key"           # set via VLM_API_KEY env variable
BASE    = "https://vlmapi.vlmdata.com"
HEADERS = {"X-VLM-API-Key": API_KEY}

def get(path, **params):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params or None, timeout=15)
    r.raise_for_status()
    return r.json()
```

---

## Standard Response Envelope

Every endpoint adds these fields to the JSON response:

| Field | Type | Notes |
|---|---|---|
| `cached` | bool | `true` = served from in-memory cache |
| `stale` | bool | `true` = upstream unreachable, using expired cache |
| `stale_age_seconds` | int | Only present when `stale: true` |

The gateway **never returns an error** if stale data is available — stale data with `stale: true` is always preferable to a 503.

---

## Valid Commodity Codes

All commodity-specific endpoints use these codes (case-insensitive):

| Code | Commodity | Exchange |
|---|---|---|
| `CT` | Cotton No. 2 | ICE Futures U.S. |
| `KC` | Coffee C | ICE Futures U.S. |
| `CC` | Cocoa | ICE Futures U.S. |
| `SB` | Sugar No. 11 | ICE Futures U.S. |

---

## Quick Reference

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service health check (no auth required) |
| GET | `/v1/catalog` | Machine-readable endpoint catalog |
| GET | `/v1/cot/{commodity}/latest` | Latest CFTC COT report — disaggregated + legacy + supplemental |
| GET | `/v1/cot/cotton/shaped` | Cotton COT shaped for market.js |
| GET | `/v1/openinterest/daily` | Latest-date futures OI + options OI, all commodities |
| GET | `/v1/openinterest/{commodity}` | Latest-date futures OI, one commodity |
| GET | `/v1/openinterest/{commodity}/options` | Latest-date options OI + Greeks, one commodity |
| GET | `/v1/openinterest/options/history` | Filtered options OI history with Greeks |
| GET | `/v1/spreads/{commodity}` | Latest-day calendar spread OHLC, one commodity |
| GET | `/v1/spreads/{commodity}/history` | Historical calendar spread OHLC with filters |
| GET | `/v1/github/oi-dashboard/data/spread_ohlc.csv` | Full spread_ohlc.csv passthrough |
| GET | `/v1/certstock/latest` | Full ICE certified cotton stock history |
| GET | `/v1/certstock/summary` | Certified stock KPIs — 52w range, year-ago, series |
| GET | `/v1/cottonbasis/latest` | Live cotton basis records |
| GET | `/v1/oncall/current` | Latest CFTC cotton on-call report |
| GET | `/v1/fibershare/latest` | Cotton vs synthetic fiber market share |
| GET | `/v1/rain/stations` | Texas rainfall history + forecast, all stations |
| GET | `/v1/rain/live` | Live NWS precipitation totals, ~181 Texas stations |
| GET | `/v1/cottonwx/stations` | Cotton belt weather — 68 stations, obs + forecast + drought |
| GET | `/v1/specflow/latest` | CTA/Spec fund positioning signal for CT, KC, CC, SB |
| GET | `/v1/wasde/full-data` | Full WASDE cotton balance sheets — all regions, 20 years |
| GET | `/v1/wasde/schedule` | 2026 WASDE release schedule |
| GET | `/v1/wasde/shaped` | WASDE shaped for market.js |
| GET | `/v1/exportsales/latest` | USDA weekly cotton export sales summary |
| GET | `/v1/exportsales/history` | Detailed export sales with country breakdown |
| GET | `/v1/exportsales/countries` | Weekly export sales by destination country |
| GET | `/v1/ctplanted/latest` | Cotton planting progress, conditions, drought, scenarios |
| GET | `/v1/exportsworksheet/latest` | Latest Census Bureau + USDA FAS export snapshot |
| GET | `/v1/ewr/latest` | ICE Electronic Warehouse Receipts by state |
| GET | `/v1/ewrworksheet/latest` | Latest EWR + shipments combined snapshot |
| GET | `/v1/ewrworksheet/data` | Full EWR worksheet history |
| GET | `/v1/productionforecast/scenarios` | US cotton production acreage scenarios |
| GET | `/v1/heatunits/stations` | Cotton belt heat unit station list |
| GET | `/v1/heatunits/latest` | Latest CHU readings per station |
| GET | `/v1/newsletters` | VLM newsletter index with optional search |
| GET | `/v1/newsletters/{slug}` | Single newsletter with full extracted PDF text |
| GET | `/v1/chartanalysis/info` | Chart analysis metadata |
| GET | `/v1/github/{repo}/{path}` | Generic GitHub file passthrough |
| GET | `/v1/gex/CT/daily` | **(planned)** GEX gamma terrain for the CT Dec complex, one trade date |
| GET | `/v1/gex/CT/latest` | **(planned)** Latest GEX trade-date pointer (metadata only) |
| GET | `/v1/gex/CT/history` | **(planned)** Historical gamma-surface backtest, one row per trade date |
| GET | `/v1/gex/CT/convexity` | **(planned)** ATM-gamma + σ-band convexity time-series |
| GET | `/v1/flow/CT/parsed` | **(planned)** Structured parsed CT options flow from EOD drops |
| GET | `/v1/gex/CT/synopsis` | **(planned)** Weekly gamma + flow synopsis (plain text) |

> The six `/v1/gex/*` and `/v1/flow/*` rows above are **PLANNED — design spec, not yet implemented** (no route exists in the gateway as of 2026-06-16). They are documented in full under "GEX & Options Flow (PLANNED)" near the end of this file so consumers can build against the agreed schema ahead of deployment.

---

---

# OI Dashboard Data Sources

The following three sections document the source files that power the Open Interest endpoints. All files live in the private GitHub repo `vlmsofts/oi-dashboard` under `data/`. The gateway reads them via `github_reader.read_file()`, caches for 300 seconds, and falls back to stale cache on upstream failure.

**Update cadence:** All three files are appended by `vlm_master_fetch.py` (Windows Task Scheduler, Monday–Friday ~09:35 EST via Bloomberg `blpapi`). Each job is idempotent — it checks whether today's date already exists before writing.

---

## Source File 1 — `data/oi_data.csv`

**What it is:** Daily futures open interest, full OHLC, and EFP/EFS/block volume for 40 month-specific generic Bloomberg contracts across all 4 commodities.

**History:** 2008-01-02 to present  
**Rows per day:** 40 (10 contracts × 4 commodities)  
**Total size:** ~15MB and growing ~11MB/year

**Tickers covered (40 total):**

| Commodity | Contracts (10 per commodity) |
|---|---|
| CT | CTMAR1, CTMAR2, CTMAY1, CTMAY2, CTJUL1, CTJUL2, CTOCT1, CTOCT2, CTDEC1, CTDEC2 |
| KC | KCMAR1, KCMAR2, KCMAY1, KCMAY2, KCJUL1, KCJUL2, KCSEP1, KCSEP2, KCDEC1, KCDEC2 |
| CC | CCMAR1, CCMAR2, CCMAY1, CCMAY2, CCJUL1, CCJUL2, CCSEP1, CCSEP2, CCDEC1, CCDEC2 |
| SB | SBMAR1, SBMAR2, SBMAY1, SBMAY2, SBJUL1, SBJUL2, SBOCT1, SBOCT2 *(no Dec — SB has 4 delivery months)* |

**Column schema (16 columns, exact order):**

```
date, commodity, contract, bbg_ticker,
settle, open_int, oi_chg, first_notice, last_trade,
high, low, open, volume,
efp_volume, efs_volume, block_volume
```

**Field definitions:**

| Field | Type | Bloomberg Source | Notes |
|---|---|---|---|
| `date` | YYYY-MM-DD | — | Trade/settlement date. NOT Bloomberg release date. |
| `commodity` | string | — | CT, KC, CC, SB |
| `contract` | string | — | Month-specific generic label e.g. `CTJUL1`, `CTJUL2` |
| `bbg_ticker` | string | — | Full Bloomberg ticker e.g. `CTJUL1 Comdty` |
| `settle` | float | `PX_LAST` | EOD settlement price |
| `open_int` | int | `OPEN_INT` | Open interest in contracts |
| `oi_chg` | int | — | Day-over-day OI change (blank on first row per ticker) |
| `first_notice` | YYYY-MM-DD | `FUT_NOTICE_FIRST` | First notice date (blank when not yet set) |
| `last_trade` | YYYY-MM-DD | `LAST_TRADEABLE_DT` | Last tradeable date (blank when not yet set) |
| `high` | float | `PX_HIGH` | Session high. Empty for rows before 2026-05-12 (column added then). |
| `low` | float | `PX_LOW` | Session low. Empty for rows before 2026-05-12. |
| `open` | float | `PX_OPEN` | Session open. Empty for rows before 2026-05-12. |
| `volume` | int | `PX_VOLUME` | Total exchange volume (outrights). Empty for rows before 2026-05-12. |
| `efp_volume` | int | `EXCHANGE_FOR_PHYSICAL_VOLUME` | EFP volume. Empty (`''`) when zero or none that session. |
| `efs_volume` | int | `EXCHANGE_FOR_SWAP_VOLUME` | EFS volume. Empty when zero. |
| `block_volume` | int | `BLOCK_TRADE_ACCUM_VOLUME` | Block trade accumulated volume. Empty when zero. |

> **Note:** Empty string `''` means zero or not reported. It does **not** mean the field is absent.

---

## Source File 2 — `data/options_oi.csv`

**What it is:** Daily options open interest and Black-76 Greeks for CT/KC/CC/SB options, computed at time of fetch.

**History:** 2025-01-03 to present  
**Rows per day:** ~2,000 (all strikes × all contract months × all commodities)  
**Total size:** ~30K rows as of 2026-06-03, growing ~88K rows/year (~11MB/year)

**Column schema (17 columns, exact order):**

```
date, commodity, security_des, contract_month, put_call, strike_px,
open_int, oi_chg, px_settle, px_volume,
expire_dt, days_to_exp, iv_pct, delta, gamma, vega, theta
```

**Field definitions:**

| Field | Type | Notes |
|---|---|---|
| `date` | YYYY-MM-DD | **Bloomberg release date = trade date + 1 business day.** See critical date convention note below. |
| `commodity` | string | CT, KC, CC, SB |
| `security_des` | string | Bloomberg security description e.g. `CTN6C 76` |
| `contract_month` | string | e.g. `Jul 2026`, `Dec 2026` |
| `put_call` | string | `C` or `P` |
| `strike_px` | float | Strike price |
| `open_int` | int | Open interest |
| `oi_chg` | int | Day-over-day OI change |
| `px_settle` | float | Settlement price |
| `px_volume` | int | Volume |
| `expire_dt` | YYYY-MM-DD | ICE-confirmed option expiry date. Available from 2026-06-02. |
| `days_to_exp` | int | Calendar days from trade date to `expire_dt`. Available from 2026-06-02. |
| `iv_pct` | float | Implied volatility % (Black-76 model). Blank when not computable. Available from 2026-06-02. |
| `delta` | float | Black-76 delta. Available from 2026-06-02. |
| `gamma` | float | Black-76 gamma. Available from 2026-06-02. |
| `vega` | float | Black-76 vega (per 1% vol move). Available from 2026-06-02. |
| `theta` | float | Black-76 theta (per calendar day). Available from 2026-06-02. |

> **CRITICAL DATE CONVENTION:** `options_oi.csv` dates are Bloomberg release dates (T+1 business day). To match a specific trade date, filter `options_oi.csv` for `date = next_business_day(target_trade_date)`. The matching futures settlement for that trade date is in `oi_data.csv` at `date = target_trade_date`. These two files' dates are always offset by one business day.

> **Greeks model:** Black-76. Risk-free rate = SOFR (`SOFRRATE Index` Bloomberg) — 3.65% as of 2026-06-01. Reviewed monthly on the 1st business day.

---

## Source File 3 — `data/spread_ohlc.csv`

**What it is:** Daily OHLC + volume for 20 generic calendar spread tickers (positions 1-2 through 5-6, auto-rolling Bloomberg generics).

**History:** 2023-06-05 to present  
**Rows per day:** 20 (5 positions × 4 commodities)

**Bloomberg tickers (20 total):**

| Commodity | Tickers |
|---|---|
| CT | `S:CTCT 1-2 Comdty` through `S:CTCT 5-6 Comdty` |
| KC | `S:KCKC 1-2 Comdty` through `S:KCKC 5-6 Comdty` |
| CC | `S:CCCC 1-2 Comdty` through `S:CCCC 5-6 Comdty` |
| SB | `S:SBSB 1-2 Comdty` through `S:SBSB 5-6 Comdty` |

> **CT spread chain note:** The Oct contract is excluded from Bloomberg's generic CT spread chain. CT spread positions therefore sequence through Mar/May/Jul/Dec only — Oct is skipped.

**Column schema (7 columns, exact order):**

```
date, commodity, spread_label, settle, high, low, volume
```

**Field definitions:**

| Field | Type | Bloomberg Source | Notes |
|---|---|---|---|
| `date` | YYYY-MM-DD | — | Trade date |
| `commodity` | string | — | CT, KC, CC, SB |
| `spread_label` | string | — | `1-2`, `2-3`, `3-4`, `4-5`, `5-6` |
| `settle` | float | `PX_LAST` | Spread settlement = front leg minus back leg |
| `high` | float | `PX_HIGH` | Session high |
| `low` | float | `PX_LOW` | Session low |
| `volume` | int | `PX_VOLUME` | Spread volume. Empty when not reported. |

---

---

# Endpoint Reference

---

## Health

### GET `/health`

No auth required. Returns service status. Use to verify deployment is live.

```python
r = requests.get(f"{BASE}/health")
data = r.json()
```

| Field | Type | Notes |
|---|---|---|
| `status` | string | `"ok"` when healthy |
| `dashboards` | int | Number of registered dashboards (18) |
| `cache_stats` | object | In-memory cache hit/miss counts |

---

## COT — Commitments of Traders

**Source:** `github:vlmsofts/cot-dashboard/data/cot_history.json`  
**Update:** Weekly — CFTC releases Fridays, data as of prior Tuesday  
**Cache TTL:** 3600s  

---

### GET `/v1/cot/{commodity}/latest`

Full COT report for one commodity. Contains disaggregated, legacy TFF, and supplemental data, nested by report type and crop split. `{commodity}` = `cotton | sugar | coffee | cocoa` (case-insensitive).

```python
data = get("/v1/cot/cotton/latest")
# data["data"]["data"]["disaggregated"]["combined"]["futures_only"][0]
# → most recent week's disaggregated futures-only record
```

**Path params:** `commodity` = `cotton | sugar | coffee | cocoa`

**Response structure:**

| Field | Type | Notes |
|---|---|---|
| `data.config.commodity` | string | Canonical name e.g. `'Cotton'` |
| `data.config.last_report_date` | string ISO 8601 | Most recent CFTC report date |
| `data.config.generated_at` | string ISO 8601 | UTC build timestamp of `cot_history.json` |
| `data.config.has_crop_split` | bool | `true` for Cotton/Coffee/Cocoa; `false` for Sugar |
| `data.data.disaggregated` | object | Disaggregated report; keys: `combined`, `old_crop`, `new_crop` (absent for Sugar) |
| `data.data.legacy` | object | Legacy TFF report; same `combined/old_crop/new_crop` structure |
| `data.data.supplemental` | object | Supplemental report; key: `combined` only (no crop split, no `futures_only`) |
| `data.data.{report}.{crop}.futures_only` | array | Weekly records newest-first, futures only |
| `data.data.{report}.{crop}.futures_options` | array | Weekly records newest-first, futures + options combined |
| `data.data.{report}.{crop}.{series}[].report_date` | string YYYY-MM-DD | CFTC report date for this row |
| `data.data.{report}.{crop}.{series}[].open_interest` | number | Total open interest |
| `data.data.{report}.{crop}.{series}[].chg_open_interest` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].producer_long` | number | Producer/merchant gross long |
| `data.data.{report}.{crop}.{series}[].producer_short` | number | Producer/merchant gross short |
| `data.data.{report}.{crop}.{series}[].producer_net` | number | Net (long − short) |
| `data.data.{report}.{crop}.{series}[].chg_producer_long` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_producer_short` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_producer_net` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].swap_long` | number | Swap dealer gross long |
| `data.data.{report}.{crop}.{series}[].swap_short` | number | Swap dealer gross short |
| `data.data.{report}.{crop}.{series}[].swap_spread` | number | Swap dealer spreading |
| `data.data.{report}.{crop}.{series}[].swap_net` | number | Swap dealer net |
| `data.data.{report}.{crop}.{series}[].chg_swap_long` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_swap_short` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_swap_spread` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_swap_net` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].mm_long` | number | Managed money gross long |
| `data.data.{report}.{crop}.{series}[].mm_short` | number | Managed money gross short |
| `data.data.{report}.{crop}.{series}[].mm_spread` | number | Managed money spreading |
| `data.data.{report}.{crop}.{series}[].mm_net` | number | Managed money net |
| `data.data.{report}.{crop}.{series}[].mm_net_pct_oi` | number | MM net as % of total OI |
| `data.data.{report}.{crop}.{series}[].chg_mm_long` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_mm_short` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_mm_spread` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_mm_net` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].other_long` | number | Other reportables gross long |
| `data.data.{report}.{crop}.{series}[].other_short` | number | Other reportables gross short |
| `data.data.{report}.{crop}.{series}[].other_spread` | number | Other reportables spreading |
| `data.data.{report}.{crop}.{series}[].other_net` | number | Other reportables net |
| `data.data.{report}.{crop}.{series}[].chg_other_long` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_other_short` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_other_spread` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_other_net` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].nonrep_long` | number | Non-reportable gross long |
| `data.data.{report}.{crop}.{series}[].nonrep_short` | number | Non-reportable gross short |
| `data.data.{report}.{crop}.{series}[].nonrep_net` | number | Non-reportable net |
| `data.data.{report}.{crop}.{series}[].chg_nonrep_long` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_nonrep_short` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].chg_nonrep_net` | number | WoW change |
| `data.data.{report}.{crop}.{series}[].traders_producer_long` | number | Producer long trader count |
| `data.data.{report}.{crop}.{series}[].traders_producer_short` | number | Producer short trader count |
| `data.data.{report}.{crop}.{series}[].traders_swap_long` | number | |
| `data.data.{report}.{crop}.{series}[].traders_swap_short` | number | |
| `data.data.{report}.{crop}.{series}[].traders_swap_spread` | number | |
| `data.data.{report}.{crop}.{series}[].traders_mm_long` | number | |
| `data.data.{report}.{crop}.{series}[].traders_mm_short` | number | |
| `data.data.{report}.{crop}.{series}[].traders_mm_spread` | number | |
| `data.data.{report}.{crop}.{series}[].traders_other_long` | number | |
| `data.data.{report}.{crop}.{series}[].traders_other_short` | number | |
| `data.data.{report}.{crop}.{series}[].traders_other_spread` | number | |
| `data.data.{report}.{crop}.{series}[].chg_traders_*` | number | WoW change for all 11 trader count fields above |
| `data.data.legacy.{crop}.{series}[].commercial_long` | number | Legacy report only — commercial gross long |
| `data.data.legacy.{crop}.{series}[].commercial_short` | number | Legacy report only — commercial gross short |
| `data.data.legacy.{crop}.{series}[].commercial_net` | number | Legacy report only |
| `data.data.legacy.{crop}.{series}[].noncommercial_long` | number | Legacy report only |
| `data.data.legacy.{crop}.{series}[].noncommercial_short` | number | Legacy report only |
| `data.data.legacy.{crop}.{series}[].noncommercial_spread` | number | Legacy report only |
| `data.data.legacy.{crop}.{series}[].noncommercial_net` | number | Legacy report only |
| `data.data.supplemental.combined.futures_options[].index_long` | number | Supplemental only — index trader gross long |
| `data.data.supplemental.combined.futures_options[].index_short` | number | Supplemental only — index trader gross short |
| `data.data.supplemental.combined.futures_options[].index_net` | number | Supplemental only |

---

### GET `/v1/cot/cotton/shaped`

Cotton COT shaped for `market.js`. Returns the single latest `disaggregated.combined.futures_only[0]` record plus a `chg_oi` alias.

```python
data = get("/v1/cot/cotton/shaped")
# data["data"]["Cotton"]["mm_net"]  → MM net contracts, latest week
```

| Field | Type | Notes |
|---|---|---|
| `as_of` | string YYYY-MM-DD | `report_date` of the latest record |
| `data.Cotton` | object | All fields from `disaggregated.combined.futures_only[0]` |
| `data.Cotton.chg_oi` | number | Alias for `chg_open_interest` — added for `market.js` compatibility |
| `data.Cotton.report_date` | string YYYY-MM-DD | |
| `data.Cotton.open_interest` | number | |
| `data.Cotton.chg_open_interest` | number | |
| `data.Cotton.producer_long` | number | |
| `data.Cotton.producer_short` | number | |
| `data.Cotton.producer_net` | number | |
| `data.Cotton.chg_producer_*` | number | (long, short, net) |
| `data.Cotton.swap_long` | number | |
| `data.Cotton.swap_short` | number | |
| `data.Cotton.swap_spread` | number | |
| `data.Cotton.swap_net` | number | |
| `data.Cotton.chg_swap_*` | number | (long, short, spread, net) |
| `data.Cotton.mm_long` | number | |
| `data.Cotton.mm_short` | number | |
| `data.Cotton.mm_spread` | number | |
| `data.Cotton.mm_net` | number | |
| `data.Cotton.mm_net_pct_oi` | number | |
| `data.Cotton.chg_mm_*` | number | (long, short, spread, net) |
| `data.Cotton.other_long` | number | |
| `data.Cotton.other_short` | number | |
| `data.Cotton.other_spread` | number | |
| `data.Cotton.other_net` | number | |
| `data.Cotton.chg_other_*` | number | (long, short, spread, net) |
| `data.Cotton.nonrep_long` | number | |
| `data.Cotton.nonrep_short` | number | |
| `data.Cotton.nonrep_net` | number | |
| `data.Cotton.chg_nonrep_*` | number | (long, short, net) |
| `data.Cotton.traders_*` | number | All 11 trader count fields |
| `data.Cotton.chg_traders_*` | number | All 11 trader count WoW changes |

---

## Open Interest

**Source:** `github:vlmsofts/oi-dashboard/data/oi_data.csv` + `options_oi.csv`  
**Update:** Daily ~09:35 EST weekdays  
**Cache TTL:** 300s  

> See "OI Dashboard Data Sources" section above for complete field definitions and date conventions.

---

### GET `/v1/openinterest/daily`

Latest-date futures OI + options OI for all 4 commodities. Each dataset is independently filtered to its own max date — `oi_last_date` and `options_last_date` **will differ** during the 2–3 minute partial update window between futures push and options push.

```python
data = get("/v1/openinterest/daily")
futures_rows  = data["oi_data"]      # latest day, all 4 commodities, 40 contracts
options_rows  = data["options_oi"]   # latest options day, all 4 commodities
oi_date       = data["oi_last_date"]
opt_date      = data["options_last_date"]
```

| Field | Type | Notes |
|---|---|---|
| `oi_last_date` | string YYYY-MM-DD | Max date in `oi_data.csv` |
| `options_last_date` | string YYYY-MM-DD | Max date in `options_oi.csv` — may differ from `oi_last_date` |
| `oi_data` | array | Futures rows for `oi_last_date`, all commodities. 16 columns per row — see oi_data.csv schema above. |
| `options_oi` | array | Options rows for `options_last_date`, all commodities. 17 columns per row — see options_oi.csv schema above. |

---

### GET `/v1/openinterest/{commodity}`

Latest-date futures OI for one commodity. Returns only the max date rows for that commodity from `oi_data.csv`.

```python
data = get("/v1/openinterest/CT")
# data["data"] → list of CT rows (10 contracts) for latest trading day
```

**Path params:** `commodity` = `CT | KC | CC | SB` (case-insensitive)

| Field | Type | Notes |
|---|---|---|
| `last_date` | string YYYY-MM-DD | Max date in `oi_data.csv` for this commodity |
| `commodity` | string | Uppercase e.g. `CT` |
| `data` | array | Latest-date rows — same 16 columns as `oi_data.csv`. Up to 10 rows per commodity. |

---

### GET `/v1/openinterest/{commodity}/options`

Latest-date options OI + Greeks for one commodity. Returns all strikes and contract months for max options date. Returns `400` for invalid commodity.

```python
data = get("/v1/openinterest/CT/options")
# data["data"] → all CT option rows for latest options date
# data["row_count"] → number of rows returned
```

**Path params:** `commodity` = `CT | KC | CC | SB` (case-insensitive)  
**Returns 400** if commodity is not in `{CT, KC, CC, SB}`.

| Field | Type | Notes |
|---|---|---|
| `last_date` | string YYYY-MM-DD | Max options date for this commodity |
| `commodity` | string | Uppercase e.g. `CT` |
| `row_count` | int | Number of rows in `data` |
| `data` | array | Latest-date options rows — all 17 columns including Greeks. See options_oi.csv schema above. |

---

### GET `/v1/openinterest/options/history`

Filtered options OI history with Greeks. All query params optional — omit all to return the full file. Returns `400` for invalid `commodity` or `pc` values.

```python
# CT calls expiring Jul 2026, from 2026-01-01
data = get("/v1/openinterest/options/history",
           commodity="CT", pc="C", month="Jul 2026", **{"from": "2026-01-01"})
rows = data["rows"]

# All KC options in a date window
data = get("/v1/openinterest/options/history",
           commodity="KC", **{"from": "2026-05-01", "to": "2026-06-03"})
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `commodity` | string | `CT \| KC \| CC \| SB` — filter to one commodity |
| `from` | YYYY-MM-DD | Include rows where `date >= from` |
| `to` | YYYY-MM-DD | Include rows where `date <= to` |
| `month` | string | Exact `contract_month` match e.g. `Jul 2026` |
| `strike` | float | Exact `strike_px` match |
| `pc` | string | `C` or `P` — put/call filter |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `row_count` | int | Number of matching rows |
| `rows` | array | Matching rows — all 17 options columns including Greeks |

> **Date filter note:** `options_oi.csv` dates are Bloomberg release dates (T+1). If you want all options activity **for trade date 2026-06-02**, filter `from=2026-06-03&to=2026-06-03`.

---

## Spreads — Calendar Spread OHLC

**Source:** `github:vlmsofts/oi-dashboard/data/spread_ohlc.csv`  
**Update:** Daily ~09:35 EST weekdays  
**Cache TTL:** 300s  

> See "OI Dashboard Data Sources — Source File 3" above for ticker list and CT Oct-exclusion note.

---

### GET `/v1/spreads/{commodity}`

Latest day's calendar spread OHLC for one commodity — all 5 spread positions. Returns `400` for invalid commodity.

```python
data = get("/v1/spreads/CT")
# data["data"] → 5 rows: 1-2, 2-3, 3-4, 4-5, 5-6 for latest day
```

**Path params:** `commodity` = `CT | KC | CC | SB` (case-insensitive)

| Field | Type | Notes |
|---|---|---|
| `last_date` | string YYYY-MM-DD | Latest date in file for this commodity |
| `commodity` | string | Uppercase |
| `data` | array | Up to 5 spread rows for `last_date` |
| `data[].date` | string YYYY-MM-DD | Trade date |
| `data[].commodity` | string | |
| `data[].spread_label` | string | `1-2` \| `2-3` \| `3-4` \| `4-5` \| `5-6` |
| `data[].settle` | string float | Spread settlement (near leg − far leg) |
| `data[].high` | string float | Session high |
| `data[].low` | string float | Session low |
| `data[].volume` | string int | Spread volume. Empty when not reported. |

---

### GET `/v1/spreads/{commodity}/history`

Historical calendar spread OHLC for one commodity. All query params optional.

```python
# CT 1-2 spread from Jan 2026
data = get("/v1/spreads/CT/history", spread="1-2", **{"from": "2026-01-01"})
rows = data["rows"]
```

**Path params:** `commodity` = `CT | KC | CC | SB`  
**Query params:**

| Param | Type | Notes |
|---|---|---|
| `from` | YYYY-MM-DD | Include rows where `date >= from` |
| `to` | YYYY-MM-DD | Include rows where `date <= to` |
| `spread` | string | Exact `spread_label` match e.g. `1-2` |

| Field | Type | Notes |
|---|---|---|
| `commodity` | string | Uppercase |
| `row_count` | int | Number of matching rows |
| `rows` | array | Matching rows — all 7 columns |

---

### GET `/v1/github/oi-dashboard/data/spread_ohlc.csv`

Generic passthrough — full `spread_ohlc.csv` as JSON rows. No filtering.

```python
data = get("/v1/github/oi-dashboard/data/spread_ohlc.csv")
all_rows = data["content"]  # full history from 2023-06-05
```

| Field | Type | Notes |
|---|---|---|
| `content` | array | Full file — all rows, all 7 columns |

---

## Certified Stock

**Source:** `github:vlmsofts/cert-stock-dashboard/HistoricalCertifiedStockReport.csv`  
**Update:** Daily on trading days  
**Cache TTL:** 300s  

---

### GET `/v1/certstock/latest`

Full certified stock history as CSV rows.

```python
data = get("/v1/certstock/latest")
rows = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `data` | array | All historical rows |
| `data[].Date Posted` | string YYYY-MM-DD | Report date |
| `data[].DALLAS/FT. WORTH, TX` | string int | Bales at DFW |
| `data[].GALVESTON, TX` | string int | Bales at Galveston |
| `data[].GREENVILLE, SC` | string int | Bales at Greenville |
| `data[].HOUSTON, TX` | string int | Bales at Houston |
| `data[].MEMPHIS, TN` | string int | Bales at Memphis |
| `data[].Total` | string int | Total certified bales |

---

### GET `/v1/certstock/summary`

Computed KPIs — latest total, 52-week range, year-ago comparison, by-location breakdown, full daily series.

```python
data = get("/v1/certstock/summary")
```

| Field | Type | Notes |
|---|---|---|
| `as_of` | string YYYY-MM-DD | Latest report date |
| `total` | int | Latest total certified bales |
| `chg_1d` | int | Change from prior day |
| `hi_52w` | int | 52-week high |
| `lo_52w` | int | 52-week low |
| `hi_all` | int | All-time high |
| `hi_all_date` | string YYYY-MM-DD | Date of all-time high |
| `ya_total` | int | Year-ago total |
| `ya_date` | string YYYY-MM-DD | Year-ago date |
| `locations` | array | Per-location breakdown — `{name, short, bales, chg}` |
| `series` | array | Full daily history — `{date, DFW, GAL, GSP, HOU, MEM, total}` |

---

## ICE Monday Reports

The five ICE cert-stock Monday reports, published weekly after Monday settlement.
All served by `routes/ice.py` `_serve()`, reading the **`vlmsofts/cotton-carry`**
GitHub repo verbatim via `github_reader`. **Cache TTL:** 300s. Auth: `X-VLM-API-Key`.
`data` is the file's top-level object directly (use `.data`, not `.data.data`).

**Source:** `github:vlmsofts/cotton-carry/data/ice/*.json` — produced by the carry
ICE dispatcher (`ice_reports_dispatcher.py`) from the ICE EWR CSV exports each Monday.

### GET `/v1/ice/grade-staple/latest`

Cert-stock quality matrix: bales by color × leaf × staple length, nationally **and per delivery point**.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/ice/grade-staple/latest" | jq '.data'
# data["grades"][0]           → {color, leaf, s33, s34, s35, s36, s37plus, total}  (national)
# data["grand_total"]         → national total bales
# data["grade_by_point"]["DFW"]["grades"]  → same grade rows scoped to Dallas/Ft. Worth
# data["grade_by_point"]["DFW"]["grand_total"]  → DFW total bales
```

**Source:** `github:vlmsofts/cotton-carry/data/ice/grade_staple_latest.json`
**Fields:** `report_date`, `source`, `grades` (array of `{color, leaf, s33, s34, s35, s36, s37plus, total}` — national), `grand_total`, `anomaly_flags` (`off_color_present`, `high_leaf_present`, `short_staple_present`, `details[]`), and **`grade_by_point`** — object keyed by delivery point `DFW`/`GAL`/`GSP`/`HOU`/`MEM`, each `{grand_total: int, grades: [...]}` with the same grade-row shape scoped to that point. Empty points (e.g. `GSP` with no stock) appear as `{grand_total: 0, grades: []}`. Served verbatim, so `grade_by_point` is always included when present.

### GET `/v1/ice/strength/latest`

Strength (grams-per-tex) distribution by delivery point, with weak-cotton discount flag.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/ice/strength/latest" | jq '.data'
# data["national_totals"]["gpt_30plus"]           → bales >=30 g/tex
# data["discount_flag"]["weak_cotton_present"]     → bool, Rule 10.22(e)(iii) trigger
```

**Source:** `github:vlmsofts/cotton-carry/data/ice/strength_latest.json`
**Fields:** `report_date`, `source`, `by_delivery_point` (keyed `DFW/GAL/GSP/HOU/MEM`, each `{gpt_25, gpt_26, gpt_27, gpt_28, gpt_29, gpt_30plus, total}` bales), `national_totals` (same buckets), `discount_flag` (`weak_cotton_present`, `gpt_25_bales`, `note`).

### GET `/v1/ice/warehouse/latest`

Per-warehouse cert-stock bale counts (full warehouse list).

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/ice/warehouse/latest" | jq '.data'
# data["warehouses"][0]              → {number, name, bales}  (sorted desc by bales)
# data["total_bales"], data["warehouse_count_with_stock"]
```

**Source:** `github:vlmsofts/cotton-carry/data/ice/warehouse_latest.json`
**Fields:** `report_date`, `source`, `warehouses` (array of `{number, name, bales}`), `total_bales`, `warehouse_count_with_stock`.

### GET `/v1/ice/yog/latest`

Year-of-growth (crop-year) breakdown by delivery point.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/ice/yog/latest" | jq '.data'
# data["national_totals_by_crop_year"]["2025"]                  → total 2025-crop bales
# data["by_delivery_point"]["DALLAS/FT. WORTH, TX"]["2025"]      → 2025-crop bales at DFW
```

**Source:** `github:vlmsofts/cotton-carry/data/ice/yog_latest.json`
**Fields:** `report_date`, `source`, `by_delivery_point` (keyed by full DP name, each `{<crop_year>: bales, ..., total}`), `national_totals_by_crop_year`.

### GET `/v1/ice/aging/latest`

Aging breakdown by months-on-certificate and delivery point, plus Rule 10.33 storage-penalty tiers.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/ice/aging/latest" | jq '.data'
# data["total_aging_bales"]              → total aged bales
# data["by_months_on_cert"]["4"]["DFW"]  → bales 4 months on cert at DFW
```

**Source:** `github:vlmsofts/cotton-carry/data/ice/aging_latest.json`
**Fields:** `report_date`, `source`, `by_months_on_cert` (keyed by month count, each `{DFW, GAL, GSP, HOU, MEM, total}`), `rule_10_33_tiers`, `total_aging_bales`.

---

## Carry — Cost of Carry

### GET `/v1/carry/latest`

Full cotton cost-of-carry output: futures settles, calendar spreads with carry decomposition per delivery point, per-DP cost-of-carry params, cert-stock summary, EWR aging, and base-grade diffs.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/carry/latest" | jq '.data'
# data["spreads"][0]["pct_true_by_dp"]["DFW"]        → % of true carry the spread captures at DFW
# data["cost_of_carry"]["DFW"]["true_carry_pts_per_month"]  → modeled true carry pts/month
# data["financing_rate"], data["prices_stale"]
```

**Source:** `github:vlmsofts/cotton-carry/data/carry/carry_output_latest.json` (`routes/carry.py`). **TTL:** 300s.
**Fields:** `generated_at`, `price_date`, `prices_stale`, `financing_rate`, `sofr_rate`, `credit_spread`; `contracts[]` (`{contract, settle, first_notice, last_trade}`); `spreads[]` (each with `*_by_dp` maps keyed `DFW/HOU/GAL/MEM/SC`: `interest_pts_per_month_by_dp`, `weight_penalty_pts_per_month_by_dp`, `si_carry_by_dp`, `true_carry_by_dp`, `pct_si_by_dp`, `pct_true_by_dp`, plus `ice_spread_pts`, `calendar_months`); `cost_of_carry` (keyed by DP, each `{storage_pts_per_month, interest_pts_per_month, weight_penalty_pts_per_month, si_carry_pts_per_month, true_carry_pts_per_month, aging_pct, yog_one_time_pts}`); `cert_stocks` (`{report_date, by_point, total}`); `ewr`; `base_grade_diffs`.

---

## DSQ — Daily Spot Quotations (USDA AMS)

Served by `routes/dsq.py` `_serve()`, reading `vlmsofts/cotton-carry` verbatim; underlying data from the USDA **AMS API**. **TTL:** 300s.

### GET `/v1/dsq/basis`

Regional cash basis and cash prices for two benchmark grades vs the active futures month.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/dsq/basis" | jq '.data'
# data["basis_month"]   → e.g. "Jul-26"
# data["markets"][]     → per-market basis/cash for 41-4-34 and 31-3-36 (+ "US Average" row)
```

**Source:** `github:vlmsofts/cotton-carry/data/dsq/basis_latest.json` (origin: USDA AMS API)
**Fields:** `report_date`, `source`, `basis_month`, `markets[]` (each `{market, commodity, basis_41_4_34, cash_price_41_4_34, basis_31_3_36, cash_price_31_3_36, bales_reported}`). Basis in points; cash in cents/lb.

### GET `/v1/dsq/regional-cash`

Regional cash quotes for the same two grades, each with its own futures reference month, plus adjusted world price.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/dsq/regional-cash" | jq '.data'
# data["regions"][]        → per-region cents/lb + basis pts for 41-4-34 and 31-3-36
# data["adj_world_price"]  → AWP (nullable), data["awp_week"]
```

**Source:** `github:vlmsofts/cotton-carry/data/dsq/regional_cash_latest.json` (origin: USDA AMS API)
**Fields:** `report_date`, `source`, `regions[]` (`{region, basis_pts_41_4_34, futures_month_41_4_34, cents_per_lb_41_4_34, basis_pts_31_3_36, futures_month_31_3_36, cents_per_lb_31_3_36}`), `adj_world_price` (nullable), `awp_week` (nullable).

### GET `/v1/dsq/tenderable`

ICE tenderable grade premium/discount schedule (points) by color × leaf across staples 33–37, plus strength discount.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/dsq/tenderable" | jq '.data'
# data["grades"][]            → {color, leaf, staple_33..staple_37} premium/discount pts
# data["strength_discount"]   → {range:"25.0-25.9", discount_pts:-455}
```

**Source:** `github:vlmsofts/cotton-carry/data/dsq/tenderable_latest.json` (origin: USDA AMS API)
**Fields:** `report_date`, `source`, `grades[]` (`{color, leaf, staple_33, staple_34, staple_35, staple_36, staple_37}` — color/leaf are strings, e.g. leaf `"1-2"`; values in points), `strength_discount` (`{range, discount_pts}`).

---

## Cotton Composite Snapshots

### GET `/v1/cotton/snapshot`

Parallel bundle of all nine `cotton-carry` repo files in one call. Never fails entirely — a missing component is `null`.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/cotton/snapshot" | jq '.data'
# data["carry"]             → full carry_output_latest.json (or null)
# data["ice_grade_staple"]  → grade_staple_latest.json (incl. grade_by_point)
# top-level .available / .unavailable list which components loaded
```

**Source:** `github:vlmsofts/cotton-carry` (9 files): `carry`, `dsq_tenderable`, `dsq_regional_cash`, `dsq_basis`, `ice_aging`, `ice_grade_staple`, `ice_strength`, `ice_yog`, `ice_warehouse`.
**Envelope (non-standard):** `{"data": {<component>: <content|null>}, "components": [...], "available": [...], "unavailable": [...]}`.

### GET `/v1/cotton/market-snapshot`

Broad cross-source market composite (10 sub-sources across multiple repos/services). Each failure is isolated per key.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/cotton/market-snapshot" | jq '.data'
# data["cot"]                       → cotton COT latest
# data["futures"]["front_settle"]   → CT front-month settle
# data["cert_stocks"]["total"]      → latest cert-stock total bales
# .data_freshness                   → per-source date stamps
```

**Sources (10):** `carry` (cotton-carry), `cot` (cot-dashboard), `futures` (oi-dashboard `oi_data.csv` CT), `cert_stocks` (cert-stock-dashboard CSV), `on_call` (cotton-on-call-dashboard), `wasde` (wasde-dashboard), `export_sales` (export-sales-dashboard), `ewr_aging` (**railway** `dashboard.ewr.vlmdata.com/api/latest`), `cash_basis` (cotton-carry dsq basis), `spec_flow` (cta-monitor).
**Envelope (non-standard):** `{"data": {<key>: <payload|{"error":...}>}, "data_freshness": {...}, "components": [...], "available": [...], "unavailable": [...]}`.

---

## COT — Cotton MM History (lean)

### GET `/v1/cot/cotton/history`

Lean cotton managed-money net-position history for percentile/z-score calcs — newest-first, only `{report_date, mm_net, mm_spread}` per week.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/cot/cotton/history?weeks=104" | jq '.data'
# data[0] → newest week: {report_date, mm_net, mm_spread}
```

**Source:** `github:vlmsofts/cot-dashboard/data/cot_history.json`, path `commodities.Cotton.data.disaggregated.combined.futures_only`. **TTL:** 3600s.
**Query params:** `weeks` (default 52, clamped to max 700; invalid → 52).
**Envelope:** `{"commodity":"Cotton", "weeks": N, "data": [{report_date, mm_net, mm_spread}, ...], "cached", "stale"}` (newest-first).

---

## Production Forecast — Model

### GET `/v1/productionforecast/model`

Per-state cotton production model forecast (yield, abandonment, production — predicted and actual) for the current forecast year, one row per state.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/productionforecast/model" | jq '.data'
# data[0]["production_pred_bales"] → predicted production (bales), float or null
```

**Source:** `github:vlmsofts/production-forecast-dashboard/processed/model_forecast_26_27.csv`. **TTL:** 3600s.
**Fields:** array of per-state rows. Strings: `forecast_year`, `state`. Floats (null when blank, e.g. `*_actual` pre-harvest): `yield_pred`, `yield_actual`, `aband_pred`, `aband_actual`, `planted_acres`, `aband_used`, `harvested_pred`, `production_pred_bales`, `production_actual_bales`.

---

## Signals

Served by `routes/signals.py` `_serve()`, reading CSVs from GitHub **`oi-dashboard`**. Rows typed (numeric→float, blanks→null; `date` string), returned **newest-first**. **TTL:** 3600s.
**Shared query params:** `date=YYYY-MM-DD` (exact-match row, takes precedence) or `rows=N` (latest N). Neither → full series.
**Envelope (non-standard order):** `{"cached", "stale", "data": [...]}`.

### GET `/v1/signals/macro`

Daily macro/cross-asset signals relevant to cotton (FX, VIX, DXY, freight, Brazil/India/Aussie refs).

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/signals/macro?rows=1" | jq '.data'
# data[0] → latest row {date, vix, dxy, ...}
```

**Source:** `github:vlmsofts/oi-dashboard/data/signals/macro_signals.csv`
**Fields per row:** `date` + floats `vix, dxy, psf, cf1, usdcny, brl, inr, aud, bdiy, cf1_usd_cents_lb, brl_per_usd, inr_per_usd, aud_per_usd`.

### GET `/v1/signals/backfill`

VLM daily cotton signal backfill — CT settles/spreads, IV/HV vol metrics, synthetic-interest carry approximations, and 1-year z-scores.

```bash
curl -s -H "X-VLM-API-Key: $KEY" "https://vlmapi.vlmdata.com/v1/signals/backfill?rows=30" | jq '.data'
# data[0]["atm_iv_zscore_1yr"] → 1yr z-score of ATM IV
```

**Source:** `github:vlmsofts/oi-dashboard/data/signals/vlm_signal_backfill.csv`
**Fields per row:** `date` + floats `ct1_close, ct2_close, ct3_close, atm_iv_30d, ct1_ct2_spread, ct2_ct3_spread, si_carry_approx, pct_si_approx, hv30, hv60, iv_hv30_ratio, atm_iv_zscore_1yr, iv_hv30_ratio_zscore, pct_si_zscore_1yr, ct1_ct2_zscore`.

---

## Cotton Basis

**Source:** `railway:dashboard.cottonbasis.vlmdata.com/api/basis`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/cottonbasis/latest`

Up to 5000 basis records ordered by `report_date` DESC. All 8 filter params forwarded to upstream. No params = most recent 5000 records.

```python
# Most recent records for Texas
data = get("/v1/cottonbasis/latest", region="Texas")

# All records for a specific date
data = get("/v1/cottonbasis/latest", date="2026-06-02")
```

**Query params:**

| Param | Notes |
|---|---|
| `date` | YYYY-MM-DD — exact `report_date` match |
| `date_from` | YYYY-MM-DD — `report_date >= date_from` |
| `date_to` | YYYY-MM-DD — `report_date <= date_to` |
| `region` | String e.g. `Texas`, `Memphis` |
| `color` | Color grade filter |
| `leaf` | Leaf grade filter |
| `staple` | Staple length (integer string) |
| `cotton_type` | Cotton type filter |

| Field | Type | Notes |
|---|---|---|
| `data` | array | Basis records |
| `data[].report_date` | string YYYY-MM-DD | Trade date of the basis quote |
| `data[].region` | string | Geographic region |
| `data[].cotton_type` | string | Cotton type classification |
| `data[].color` | string | Color grade |
| `data[].leaf` | string | Leaf grade |
| `data[].staple` | string int | Staple length |
| `data[].basis_points` | int | Basis in points on/off futures |
| `data[].futures_month` | string | Nearby futures delivery month |
| `data[].cents_per_lb` | float | Basis in cents per pound |

---

## Cotton On-Call

**Source:** `github:vlmsofts/cotton-on-call-dashboard/coc_data.csv`  
**Update:** Weekly (CFTC Fridays, data as of prior Friday close)  
**Cache TTL:** 3600s  

---

### GET `/v1/oncall/current`

Latest report date rows only — filtered to max `report_date` in file.

```python
data = get("/v1/oncall/current")
rows = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `report_date` | string YYYY-MM-DD | Latest report date |
| `data` | array | All rows for that date |
| `data[].report_date` | string YYYY-MM-DD | |
| `data[].release_date` | string YYYY-MM-DD | |
| `data[].report_year` | string int | |
| `data[].delivery_month` | string | e.g. `March 2026` |
| `data[].delivery_mo_name` | string | e.g. `March` |
| `data[].delivery_year` | string int | |
| `data[].unfixed_sales` | string float | Mill on-call sales (thousand bales) |
| `data[].sales_chg` | string float | Week-on-week change |
| `data[].unfixed_purch` | string float | Merchant on-call purchases (thousand bales) |
| `data[].purch_chg` | string float | Week-on-week change |
| `data[].open_interest` | string int | |
| `data[].oi_chg` | string int | |

---

## Fiber Share

**Source:** `github:vlmsofts/fibershare-dashboard/static/fibershare_latest.json`  
**Update:** Periodic (when file is updated)  
**Cache TTL:** 300s  

---

### GET `/v1/fibershare/latest`

Fiber share summary. Optional `include` param expands additional sections.

```python
data = get("/v1/fibershare/latest")                    # core summary only
data = get("/v1/fibershare/latest", include="full")    # all sections
```

**Query params:** `include` = `monthly | countries | elasticity | full` (comma-separated)

| Field | Type | Notes |
|---|---|---|
| `as_of` | string | Period this data covers |
| `generated_utc` | string ISO datetime | When file was built |
| `latest_period` | object | Most recent period data |
| `summary` | object | Key KPIs |
| `monthly` | array | Monthly series — only if `?include=monthly` or `full` |
| `countries` | array | By-country breakdown — only if `?include=countries` or `full` |
| `elasticity` | object | Elasticity data — only if `?include=elasticity` or `full` |

---

## Texas Rainfall

**Source:** `github:vlmsofts/rain-dashboard/data/rain_history.csv + nws_live.json`  
**Update:** Frequently (weather data)  
**Cache TTL:** 300s  

---

### GET `/v1/rain/stations`

Station history and 16-day forecast for all Texas weather stations. **Unfiltered payload is ~53MB** — always use `?from=` to limit.

```python
# Last 30 days only
data = get("/v1/rain/stations", **{"from": "2026-05-01"})
history  = data["history"]
forecast = data["forecast"]
```

**Query params:** `from` = YYYY-MM-DD — filter history rows to `date >= from`. Forecast rows unaffected.

| Field | Type | Notes |
|---|---|---|
| `history` | array | Historical observation rows (filtered when `from` specified) |
| `history[].station` | string | Station name |
| `history[].county` | string | |
| `history[].lat` | string float | |
| `history[].lon` | string float | |
| `history[].date` | string YYYY-MM-DD | |
| `history[].precip` | string float | Actual precipitation inches |
| `history[].precip_prob` | string float | Probability of precip |
| `history[].tmax` | string float | Max temp °F |
| `history[].tmin` | string float | Min temp °F |
| `history[].forecast` | string `0\|1` | `1` = forecast row |
| `forecast` | array | Forecast rows |
| `forecast[].station` | string | |
| `forecast[].county` | string | |
| `forecast[].lat` | string float | |
| `forecast[].lon` | string float | |
| `forecast[].date` | string YYYY-MM-DD | |
| `forecast[].precip` | string float | Forecast precip inches |
| `forecast[].precip_prob` | string float | |
| `forecast[].tmax` | string float | |
| `forecast[].tmin` | string float | |
| `forecast[].windspeed` | string float | mph |
| `forecast[].conditions` | string | Weather description |
| `forecast[].forecast` | string `0\|1` | Always `1` |
| `forecast[].source` | string | Data source e.g. `wb` |

---

### GET `/v1/rain/live`

Live NWS multi-period precipitation totals for ~181 Texas and Southwest stations.

```python
data = get("/v1/rain/live")
stations = data["data"]["stations"]
# stations["Lubbock"]["hr24"] → 24-hour precip at Lubbock
```

| Field | Type | Notes |
|---|---|---|
| `as_of` | string YYYY-MM-DD | Date portion of `data.fetched_utc` |
| `data.fetched_utc` | string ISO datetime | UTC timestamp when `nws_live.json` was built |
| `data.report_time` | string | NWS report validity window |
| `data.stations` | object | ~181 stations keyed by station name |
| `data.stations.{name}.name` | string | Station name (same as key) |
| `data.stations.{name}.hr1` | number | Precipitation last 1 hour (inches) |
| `data.stations.{name}.hr3` | number | Precipitation last 3 hours (inches) |
| `data.stations.{name}.hr6` | number | Precipitation last 6 hours (inches) |
| `data.stations.{name}.hr12` | number | Precipitation last 12 hours (inches) |
| `data.stations.{name}.hr24` | number | Precipitation last 24 hours (inches) |
| `data.stations.{name}.hr48` | number | Precipitation last 48 hours (inches) |
| `data.stations.{name}.hr72` | number | Precipitation last 72 hours (inches) |
| `data.stations.{name}.hr96` | number | Precipitation last 96 hours (inches) |
| `data.stations.{name}.last_ob` | string | Last observation time (format `DD/HHmm` e.g. `02/1200`) |

---

## Cotton Weather

**Source:** `railway:dashboard.cottonwx.vlmdata.com/api/data`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/cottonwx/stations`

Full cotton belt weather dataset — 68 stations with current observations, 16-day deterministic forecast, 15-day ensemble forecast, drought, crop stages, and AI-generated narratives.

```python
data = get("/v1/cottonwx/stations")
stations   = data["stations"]       # list of 68 station objects
narratives = data["narratives"]     # AI weather narratives
```

| Field | Type | Notes |
|---|---|---|
| `stations` | array | 68 station objects |
| `stations[].id` | string | Station slug e.g. `lubbock` |
| `stations[].name` | string | Display name |
| `stations[].county` | string | |
| `stations[].state` | string | 2-letter state code |
| `stations[].lat` | number | Latitude |
| `stations[].lon` | number | Longitude |
| `stations[].fips` | string | FIPS county code |
| `stations[].region` | string | Cotton belt region e.g. `West Texas` |
| `stations[].region_id` | int | Region identifier |
| `stations[].drought_category` | string | USDM category: `D0`–`D4` or `None` |
| `stations[].last_updated` | string ISO datetime | Last data refresh for this station |
| `stations[].wb.data.current_temp` | number | Current temperature °F |
| `stations[].wb.data.dewpt` | number | Dewpoint °F |
| `stations[].wb.data.humidity` | number | Relative humidity % |
| `stations[].wb.data.wind_spd` | number | Wind speed mph |
| `stations[].wb.data.wind_gust` | number | Wind gust mph |
| `stations[].wb.data.wind_dir` | number | Wind direction degrees |
| `stations[].wb.data.clouds` | number | Cloud cover % |
| `stations[].wb.data.conditions` | string | Weather description |
| `stations[].wb.data.weather_icon` | string | Icon code |
| `stations[].wb.data.uv` | number | UV index |
| `stations[].wb.data.solar_rad` | number | Solar radiation W/m² |
| `stations[].wb.data.ghi` | number | Global horizontal irradiance |
| `stations[].wb.data.dni` | number | Direct normal irradiance |
| `stations[].wb.data.dhi` | number | Diffuse horizontal irradiance |
| `stations[].wb.data.aqi` | number | Air quality index |
| `stations[].wb.data.slp` | number | Sea level pressure hPa |
| `stations[].wb.data.today_precip` | number | Today's accumulated precip inches |
| `stations[].wb.data.seven_day_precip` | number | 7-day accumulated precip inches |
| `stations[].wb.data.observed_ts` | string | Observation timestamp |
| `stations[].wb.stale` | bool | |
| `stations[].wb_ag.et0` | number | Reference evapotranspiration inches |
| `stations[].wb_ag.soil_temp_2in_f` | number | Soil temp at 2in depth °F |
| `stations[].wb_ag.soil_temp_4in_f` | number | Soil temp at 4in depth °F |
| `stations[].wb_ag.soil_temp_8in_f` | number | Soil temp at 8in depth °F |
| `stations[].wb_fcst.data.high` | number | Today's forecast high °F |
| `stations[].wb_fcst.data.low` | number | Today's forecast low °F |
| `stations[].wb_fcst.data.pop` | number | Today's probability of precip % |
| `stations[].wb_fcst.data.clouds` | number | Cloud cover % |
| `stations[].wb_fcst.data.dewpt` | number | Dewpoint °F |
| `stations[].wb_fcst.data.uv` | number | UV index |
| `stations[].wb_fcst.data.wind_dir` | number | Wind direction degrees |
| `stations[].wb_fcst.data.wind_max` | number | Max wind speed mph |
| `stations[].wb_fcst.data.forecast_7day_precip` | number | 7-day forecast precip inches |
| `stations[].wb_fcst.data.forecast_8_15_precip` | number | 8–15 day forecast precip inches |
| `stations[].wb_fcst.data.forecast_days` | array | 16-day daily forecast |
| `stations[].wb_fcst.data.forecast_days[].date` | string YYYY-MM-DD | |
| `stations[].wb_fcst.data.forecast_days[].tmax` | number | Max temp °F |
| `stations[].wb_fcst.data.forecast_days[].tmin` | number | Min temp °F |
| `stations[].wb_fcst.data.forecast_days[].pop` | number | Probability of precip % |
| `stations[].wb_fcst.data.forecast_days[].precip` | number | Forecast precip inches |
| `stations[].wb_fcst.data.forecast_days[].wind` | number | Wind speed mph |
| `stations[].wb_fcst.stale` | bool | |
| `stations[].om_ens.data.ens_7day_mean` | number | Ensemble 7-day mean precip inches |
| `stations[].om_ens.data.ens_7day_pop` | number | Ensemble 7-day prob of precip % |
| `stations[].om_ens.data.ens_8_15_mean` | number | Ensemble 8–15 day mean precip inches |
| `stations[].om_ens.data.ens_8_15_pop` | number | Ensemble 8–15 day prob of precip % |
| `stations[].om_ens.data.forecast_ens_days` | array | 15-day ensemble forecast |
| `stations[].om_ens.data.forecast_ens_days[].date` | string YYYY-MM-DD | |
| `stations[].om_ens.data.forecast_ens_days[].mean` | number | Ensemble mean precip inches |
| `stations[].om_ens.data.forecast_ens_days[].pop` | number | Prob of precip % |
| `stations[].om_ens.stale` | bool | |
| `crop_stages` | object | Current crop stage by region — keys are region names, values are stage strings e.g. `Planting`, `Squaring`, `Flowering`, `Boll set`, `Open bolls` |
| `narratives.master` | string | Master weather narrative |
| `narratives.current_conditions` | string | |
| `narratives.short_range_precip` | string | |
| `narratives.extended_precip` | string | |
| `narratives.wpc_medium_range` | string | |
| `narratives.temperature_outlook` | string | |
| `narratives.drought_soil` | string | |
| `narratives.severe_weather` | string | |
| `narratives.seasonal_long_range` | string | |
| `narratives.station_data` | string | |
| `last_refresh.status` | string | `ok \| warn \| error` |
| `last_refresh.timestamp` | string ISO datetime | |
| `last_refresh.steps` | object | Per-step status: `{narratives, scrape, trigger}` |

---

## Spec Flow / CTA Monitor

**Source:** `github:vlmsofts/cta-monitor/data/cta_data.json`  
**Update:** Weekly  
**Cache TTL:** 300s  

---

### GET `/v1/specflow/latest`

Full CTA positioning signal for CT, KC, CC, SB. Includes model position estimate, 5 price-shock scenarios, 251-day signal history, 52-week COT dots, extremes categories.

```python
data = get("/v1/specflow/latest")
ct_signal = data["commodities"]["ct"]["signal_pct"]
ct_scenarios = data["commodities"]["ct"]["position"]["scenarios"]
```

| Field | Type | Notes |
|---|---|---|
| `generated_at` | string ISO 8601 | Timestamp when `cta_data.json` was last built |
| `commodities` | object | Per-commodity data; keys: `ct`, `sb`, `kc`, `cc` |
| `commodities.{sym}.name` | string | Full name e.g. `Cotton No.2` |
| `commodities.{sym}.signal_pct` | float | CTA signal as percentile 0–100 |
| `commodities.{sym}.signal_zscore` | float | Signal z-score vs trailing history |
| `commodities.{sym}.signal_direction` | string | `bull` or `bear` |
| `commodities.{sym}.signal_chg_1w` | float | 1-week change in raw signal |
| `commodities.{sym}.vol_20d` | float | 20-day realized volatility (annualized) |
| `commodities.{sym}.price_latest` | float | Most recent futures settle |
| `commodities.{sym}.cot_actual_latest` | int | Latest CFTC MM net contracts |
| `commodities.{sym}.cot_date` | string YYYY-MM-DD | Latest COT report date (Tuesday) |
| `commodities.{sym}.position.alpha` | float | Model regression intercept |
| `commodities.{sym}.position.beta` | float | Model regression slope |
| `commodities.{sym}.position.est_lots` | int | Model-estimated MM net today |
| `commodities.{sym}.position.actual_mm` | int | Latest CFTC MM net |
| `commodities.{sym}.position.band_1s` | float | 1-sigma band width (lots) |
| `commodities.{sym}.position.band_2s` | float | 2-sigma band width (lots) |
| `commodities.{sym}.position.signal_today` | float | Raw signal value today |
| `commodities.{sym}.position.vol_today` | float | Volatility used in today's calculation |
| `commodities.{sym}.position.price_today` | float | Price used in today's calculation |
| `commodities.{sym}.position.r2` | float | Model R² |
| `commodities.{sym}.position.scenarios` | object | 5 price-shock scenarios: `dn_2s`, `dn_1s`, `flat`, `up_1s`, `up_2s` |
| `commodities.{sym}.position.scenarios.{key}.shock_pct` | float | Price shock applied e.g. `-10.22`, `0.0`, `+5.11` |
| `commodities.{sym}.position.scenarios.{key}.price_d5` | float | Projected price after shock |
| `commodities.{sym}.position.scenarios.{key}.est_lots` | int | Estimated MM net under this scenario |
| `commodities.{sym}.position.scenarios.{key}.momentum_flow` | int | Lots from momentum signal change |
| `commodities.{sym}.position.scenarios.{key}.vol_adj` | int | Lots from vol change |
| `commodities.{sym}.position.scenarios.{key}.flow_delta` | int | Total net flow (momentum + vol) |
| `commodities.{sym}.position.scenarios.{key}.severity` | string | `INERT`, `NORMAL`, `EXTREME` etc. |
| `commodities.{sym}.chart_history.signal` | array[251] | Daily signal history |
| `commodities.{sym}.chart_history.signal[].date` | string YYYY-MM-DD | |
| `commodities.{sym}.chart_history.signal[].close` | float | Futures settle |
| `commodities.{sym}.chart_history.signal[].sig_pct` | float | Signal percentile |
| `commodities.{sym}.chart_history.signal[].sig_z` | float | Signal z-score |
| `commodities.{sym}.chart_history.cot_dots` | array[52] | Weekly COT dots |
| `commodities.{sym}.chart_history.cot_dots[].date` | string YYYY-MM-DD | COT Tuesday date |
| `commodities.{sym}.chart_history.cot_dots[].mm_net` | int | Actual CFTC MM net |
| `commodities.{sym}.extremes_categories` | object | 15 COT percentile ranks; keys: `mm_long_pct`, `mm_short_pct`, `mm_net_pct`, `mm_spread_pct`, `prod_long_pct`, `prod_short_pct`, `prod_net_pct`, `swap_long_pct`, `swap_short_pct`, `swap_net_pct`, `swap_spread_pct`, `other_long_pct`, `other_short_pct`, `other_net_pct`, `other_spread_pct` |
| `commodities.{sym}.extremes_categories.{cat}.label` | string | Display label e.g. `MM Long` |
| `commodities.{sym}.extremes_categories.{cat}.group` | string | Participant group e.g. `Managed Money` |
| `commodities.{sym}.extremes_categories.{cat}.rank` | float | Percentile rank 0–100 vs history |
| `commodities.{sym}.extremes_categories.{cat}.pct_oi` | float | Position as % of open interest |
| `commodities.{sym}.extremes_categories.{cat}.flag` | string\|null | `!! HI`, `!! LO`, `HI`, `LO`, or `null` |
| `extremes` | object | COT extremes snapshot; keys: `ct`, `sb`, `kc`, `cc` |
| `extremes.{sym}.name` | string | Full commodity name |
| `extremes.{sym}.date` | string YYYY-MM-DD | COT report date |
| `extremes.{sym}.oi` | int | Total open interest |
| `extremes.{sym}.mm_long` | int | MM gross long |
| `extremes.{sym}.mm_short` | int | MM gross short |
| `extremes.{sym}.mm_net` | int | MM net |
| `extremes.{sym}.prod_net` | int | Producer/commercial net |
| `extremes.{sym}.categories` | object | Same 15-key structure as `extremes_categories` |

---

## WASDE

**Source:** `github:vlmsofts/wasde-dashboard/data/wasde_full_data.json`  
**Update:** Monthly (~11th of each month)  
**Cache TTL:** 3600s  

---

### GET `/v1/wasde/full-data`

Full WASDE cotton balance sheets — all market years, all 22 regions, all 7 attributes.

```python
data = get("/v1/wasde/full-data")
us_2026 = data["data"]["current"]["us"]["2026/27"]
world   = data["data"]["world"]["China"]["2025/26"]
```

| Field | Type | Notes |
|---|---|---|
| `data.meta.wasde_num` | int | WASDE report number e.g. `671` |
| `data.meta.month` | string | Calendar month of release e.g. `May` |
| `data.meta.year` | string | Calendar year e.g. `2026` |
| `data.meta.generated_at` | string ISO 8601 | When `rebuild_w.py` ran |
| `data.market_years` | array[20] | Market years `YYYY/YY` ascending — `2007/08` through `2026/27` |
| `data.attributes` | array[7] | `Beginning Stocks`, `Production`, `Imports`, `Domestic Use`, `Exports`, `Loss`, `Ending Stocks` |
| `data.narrative_text` | string | Verbatim USDA cotton commentary (may contain `\n`) |
| `data.current.us` | object | US balance sheet for 3 market years: `2024/25`, `2025/26`, `2026/27` |
| `data.current.us.{year}.Planted` | number | Planted area (million acres) |
| `data.current.us.{year}.Harvested` | number | Harvested area (million acres) |
| `data.current.us.{year}.Yield` | number | Yield (lbs per harvested acre) |
| `data.current.us.{year}.BegStk` | number | Beginning stocks (million 480-lb bales) |
| `data.current.us.{year}.Prod` | number | Production (million 480-lb bales) |
| `data.current.us.{year}.Imports` | number | Imports |
| `data.current.us.{year}.Supply` | number | Total supply: BegStk + Prod + Imports |
| `data.current.us.{year}.DomUse` | number | Domestic mill use |
| `data.current.us.{year}.Exports` | number | Exports |
| `data.current.us.{year}.TotalUse` | number | Total use: DomUse + Exports |
| `data.current.us.{year}.Unacct` | number | Unaccounted residual |
| `data.current.us.{year}.EndStk` | number | Ending stocks |
| `data.current.us.{year}.FarmPrice` | number | Season-average farm price (cents/lb) |
| `data.current.world.{year}.Prod` | number | World production |
| `data.current.world.{year}.DomUse` | number | World domestic use |
| `data.current.world.{year}.Exports` | number | World exports |
| `data.current.world.{year}.Imports` | number | World imports |
| `data.current.world.{year}.BegStk` | number | World beginning stocks |
| `data.current.world.{year}.EndStk` | number | World ending stocks |
| `data.world.{region}` | object | 22 regions: `Afr. Fr. Zone`, `Australia`, `Bangladesh`, `Brazil`, `Central Asia`, `China`, `EU-27`, `European Union`, `India`, `Indonesia`, `Major Exporters`, `Major Importers`, `Mexico`, `Pakistan`, `S. Hemis.`, `Thailand`, `Total Foreign`, `Turkey`, `United States`, `Vietnam`, `World`, `World Less China` |
| `data.world.{region}.{year}` | object\|null | 20 market years. `null` if USDA did not report this region/year. |
| `data.world.{region}.{year}.'Beginning Stocks'` | number\|null | |
| `data.world.{region}.{year}.'Production'` | number\|null | |
| `data.world.{region}.{year}.'Imports'` | number\|null | |
| `data.world.{region}.{year}.'Domestic Use'` | number\|null | |
| `data.world.{region}.{year}.'Exports'` | number\|null | |
| `data.world.{region}.{year}.'Loss'` | number\|null | |
| `data.world.{region}.{year}.'Ending Stocks'` | number\|null | |

---

### GET `/v1/wasde/schedule`

2026 WASDE release schedule. Hardcoded — no upstream call.

```python
data = get("/v1/wasde/schedule")
```

| Field | Type | Notes |
|---|---|---|
| `next_release` | string YYYY-MM-DD | Next release date |
| `next_release_utc` | string ISO datetime | Next release at 12:00:00Z |
| `seconds_until` | int | Seconds until next release |
| `remaining_2026` | array | All remaining 2026 release dates |

---

### GET `/v1/wasde/shaped`

WASDE shaped for `market.js` — current year US + world, 20-year US history, country table, next release, narrative.

```python
data = get("/v1/wasde/shaped")
us_end_stocks = data["us"]["latest"]["EndStk"]
```

| Field | Type | Notes |
|---|---|---|
| `latest_label` | string | e.g. `June 2026` |
| `prev_label` | string | e.g. `WASDE 670` |
| `built_at` | string YYYY-MM-DD | Date portion of `meta.generated_at` |
| `cur_year` | string | Most recent marketing year e.g. `2026/27` |
| `attrs` | array[7] | Same 7 attributes as full-data |
| `us.latest` | object | Current year US balance sheet (13 fields: Planted, Harvested, Yield, BegStk, Prod, Imports, Supply, DomUse, Exports, TotalUse, Unacct, EndStk, FarmPrice) |
| `us.prev` | object | Prior year US balance sheet, same 13 fields |
| `us.hist` | array[20] | One entry per market year, ascending |
| `us.hist[].year` | string | e.g. `2007/08` |
| `us.hist[].Production` | number\|null | |
| `us.hist[].Exports` | number\|null | |
| `us.hist[].'Ending Stocks'` | number\|null | |
| `world.latest` | object | Current year world summary (Prod, DomUse, Exports, Imports, BegStk, EndStk) |
| `world.prev` | object | Prior year world summary, same 6 fields |
| `countries` | array[≤7] | Regions: United States, China, India, Brazil, Pakistan, Australia, World |
| `countries[].name` | string | Region name |
| `countries[].'Beginning Stocks'` | number\|null | Current year value |
| `countries[].'Production'` | number\|null | |
| `countries[].'Imports'` | number\|null | |
| `countries[].'Domestic Use'` | number\|null | |
| `countries[].'Exports'` | number\|null | |
| `countries[].'Loss'` | number\|null | |
| `countries[].'Ending Stocks'` | number\|null | |
| `next_wasde` | object\|null | `null` after final 2026 release |
| `next_wasde.date` | string YYYY-MM-DD | |
| `next_wasde.label` | string | Same as `date` |
| `next_wasde.num` | int | Current `wasde_num + 1` |
| `next_wasde.newCrop` | bool | `false` |
| `narrative` | object\|null | `null` if `narrative_text` absent |
| `narrative.title` | string | e.g. `WASDE 671 — June 2026 Cotton Commentary` |
| `narrative.text` | string | Verbatim USDA commentary |
| `narrative.source` | string | e.g. `USDA WASDE No. 671` |

---

## Export Sales

**Source:** `github:vlmsofts/vlmsofts-export-sales-dashboard/hist_weekly.csv + history.csv + hist_country.csv`  
**Update:** Weekly (USDA Thursday release)  
**Cache TTL:** 3600s  

---

### GET `/v1/exportsales/latest`

Weekly net sales and shipments — all weeks in `hist_weekly.csv`.

```python
data = get("/v1/exportsales/latest")
rows = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `as_of` | string YYYY-MM-DD | Most recent `week_date` |
| `data` | array | All weekly rows |
| `data[].week_date` | string MM/DD/YYYY | |
| `data[].marketing_year` | string | e.g. `2025/26` |
| `data[].week_num` | string int | |
| `data[].net_sales` | string float | Thousand 480-lb bales |
| `data[].exports` | string float | |
| `data[].accumulated_exports` | string float | |
| `data[].outstanding_sales` | string float | |
| `data[].next_yr_net` | string float | |
| `data[].next_yr_os` | string float | |
| `data[].total_commitment` | string float | |

---

### GET `/v1/exportsales/history`

Detailed historical export sales with commodity code and country breakdown (`history.csv`).

```python
data = get("/v1/exportsales/history")
```

| Field | Type | Notes |
|---|---|---|
| `data` | array | All rows |
| `data[].week_date` | string MM/DD/YYYY | |
| `data[].commodity_code` | string int | USDA commodity code |
| `data[].country_name` | string | |
| `data[].gross_new_sales` | string float | |
| `data[].net_sales` | string float | |
| `data[].weekly_exports` | string float | |
| `data[].outstanding_sales` | string float | |
| `data[].accumulated_exports` | string float | |
| `data[].dest_chgs` | string float | |
| `data[].buybacks_cancellations` | string float | |
| `data[].next_my_net_sales` | string float | |
| `data[].next_my_outstanding_sales` | string float | |
| `data[].tot_net_sales` | string float | |
| `data[].tot_outstanding_sales` | string float | |
| `data[].tot_accumulated_exports` | string float | |
| `data[].tot_weekly_exports` | string float | |
| `data[].tot_gross_new_sales` | string float | |
| `data[].tot_buybacks_cancellations` | string float | |
| `data[].tot_next_my_net_sales` | string float | |
| `data[].tot_next_my_outstanding_sales` | string float | |

---

### GET `/v1/exportsales/countries`

Weekly export sales by destination country (`hist_country.csv`). **Unfiltered payload is ~8.3MB** — use `?from=` to limit.

```python
data = get("/v1/exportsales/countries", **{"from": "2026-01-01"})
```

**Query params:** `from` = YYYY-MM-DD — filter to `week_date >= from`

| Field | Type | Notes |
|---|---|---|
| `data` | array | Rows (filtered if `from` specified) |
| `data[].week_date` | string MM/DD/YYYY | |
| `data[].marketing_year` | string | |
| `data[].week_num` | string int | |
| `data[].country_code` | string int | USDA destination code |
| `data[].country_name` | string | |
| `data[].net_sales` | string float | Thousand 480-lb bales |
| `data[].exports` | string float | |
| `data[].accumulated_exports` | string float | |
| `data[].outstanding_sales` | string float | |
| `data[].next_yr_net` | string float | |
| `data[].next_yr_os` | string float | |
| `data[].total_commitment` | string float | |

---

## Cotton Planted

**Source:** `railway:dashboard.ctplanted.vlmdata.com/api/data`  
**Update:** Weekly (USDA crop progress, Monday release)  
**Cache TTL:** 60s  

---

### GET `/v1/ctplanted/latest`

Full cotton planted dataset — planting progress, conditions, drought, acreage by source, VLM production scenarios, analog years.

```python
data = get("/v1/ctplanted/latest")
scenarios = data["scenarios"]
progress  = data["planting_progress"]
```

| Field | Type | Notes |
|---|---|---|
| `as_of` | string YYYY-MM-DD | Latest USDA crop progress report date |
| `crop_year` | int | Current crop year e.g. `2026` |
| `belt_dsci` | number | Drought severity and coverage index for cotton belt (0–100) |
| `planting_progress` | array | By-state planting progress |
| `planting_progress[].state` | string | 2-letter state code |
| `planting_progress[].current` | number | Current week planted % |
| `planting_progress[].prev_yr` | number | Prior year same week % |
| `planting_progress[].avg_3yr` | number | 3-year average % |
| `planting_progress[].avg_5yr` | number | 5-year average % |
| `planting_progress[].vs_prev` | number | vs prior year (ppt) |
| `planting_progress[].vs_3yr` | number | vs 3-year avg (ppt) |
| `planting_progress[].vs_5yr` | number | vs 5-year avg (ppt) |
| `planting_progress[].wow_chg` | number | Week-over-week change (ppt) |
| `crop_conditions` | array | By-state good/excellent % |
| `crop_conditions[].state` | string | |
| `crop_conditions[].ge_pct` | number | Good + excellent % |
| `crop_conditions[].prev_yr` | number | |
| `crop_conditions[].avg_3yr` | number | |
| `crop_conditions[].avg_5yr` | number | |
| `crop_conditions[].vs_prev` | number | |
| `crop_conditions[].vs_3yr` | number | |
| `crop_conditions[].vs_5yr` | number | |
| `crop_conditions[].wow_chg` | number | |
| `drought_states` | array | USDM drought by state |
| `drought_states[].state` | string | |
| `drought_states[].d2_pct` | number | D2+ drought area % |
| `drought_states[].d3_pct` | number | D3+ drought area % |
| `drought_states[].signal` | string | `MODERATE \| SEVERE \| NONE` |
| `drought_states[].vs_prior_wk` | number | Change vs prior week (ppt) |
| `acres_ncc` | array | NCC acreage estimate by state |
| `acres_ncc[].state` | string | |
| `acres_ncc[].acres` | number | Thousand acres |
| `acres_prosp` | array | USDA prospective plantings by state |
| `acres_prosp[].state` | string | |
| `acres_prosp[].acres` | number | Thousand acres |
| `acres_vlm` | array | VLM acreage estimate by state |
| `acres_vlm[].state` | string | |
| `acres_vlm[].acres` | number | Thousand acres |
| `states` | array | Current VLM acreage per state (same structure as `acres_vlm`) |
| `scenarios` | array | VLM production scenarios |
| `scenarios[].name` | string | e.g. `Base Case` |
| `scenarios[].description` | string | |
| `scenarios[].prob` | int | Probability % |
| `scenarios[].acres` | number | Planted acres (million) |
| `scenarios[].abandon_pct` | number | Abandonment % |
| `scenarios[].harv_acres` | number | Harvested acres (million) |
| `scenarios[].prod_mb` | number | Production (million bales) |
| `scenarios[].yield_lbs` | number | Yield (lbs/acre) |
| `scenarios[].price_tgt` | string | Price target range e.g. `68-72c` |
| `scenarios[].col` | string | Hex color for display |
| `analogs` | array | Historical analog years |
| `analogs[].year` | int | |
| `analogs[].d2_pct` | number | D2+ drought % that year |
| `analogs[].prod_mb` | number | Actual production that year (million bales) |
| `drought_history` | array | Full drought vs production history |
| `drought_history[].year` | int | |
| `drought_history[].d2_pct` | number | |
| `drought_history[].prod_mb` | number | |

---

## Exports Worksheet

**Source:** `railway:dashboard.exportsworksheet.vlmdata.com/api/data`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/exportsworksheet/latest`

Monthly US cotton export totals combining Census Bureau and USDA FAS data.

```python
data = get("/v1/exportsworksheet/latest")
rows = data["data"]["rows"]
```

| Field | Type | Notes |
|---|---|---|
| `data.rows` | array | Monthly export rows |
| `data.rows[].year` | int | Calendar year |
| `data.rows[].month_no` | int | 1–12 |
| `data.rows[].month` | string | Short month name e.g. `Jan` |
| `data.rows[].kg` | number | Census Bureau exports in kilograms |
| `data.rows[].cb_rb` | number | Census Bureau exports in running bales |
| `data.rows[].cb_sb` | number | Census Bureau exports in statistical bales |
| `data.rows[].cb_my_cum` | number | Census Bureau marketing year cumulative (running bales) |
| `data.rows[].cb_my_cum_sb` | number | Census Bureau marketing year cumulative (statistical bales) |
| `data.rows[].cb_cal_cum` | number | Census Bureau calendar year cumulative (running bales) |
| `data.rows[].cb_cal_cum_sb` | number | Census Bureau calendar year cumulative (statistical bales) |
| `data.rows[].esr_rb` | number | USDA FAS exports in running bales |
| `data.rows[].esr_sb` | number | USDA FAS exports in statistical bales |
| `data.rows[].esr_my_cum` | number | USDA FAS marketing year cumulative (running bales) |
| `data.rows[].esr_my_cum_sb` | number | USDA FAS marketing year cumulative (statistical bales) |
| `data.rows[].esr_cal_cum` | number | USDA FAS calendar year cumulative (running bales) |
| `data.rows[].esr_cal_cum_sb` | number | USDA FAS calendar year cumulative (statistical bales) |
| `data.rows[].diff_rb` | number | ESR vs CB difference in running bales (current month) |
| `data.rows[].diff_sb` | number | ESR vs CB difference in statistical bales |
| `data.rows[].my_diff_rb` | number | Marketing year cumulative difference (running bales) |
| `data.rows[].my_diff_sb` | number | Marketing year cumulative difference (statistical bales) |
| `data.rows[].notes` | string\|null | Editorial notes |
| `data.next_entry.year` | int | |
| `data.next_entry.month_no` | int | |
| `data.next_entry.month` | string | Short month name |
| `data.as_of` | string | Latest month available e.g. `Mar 2026` |

---

## EWR Cotton

**Source:** `railway:dashboard.ewr.vlmdata.com/api/latest`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/ewr/latest`

Latest ICE Electronic Warehouse Receipts — one row per state.

```python
data = get("/v1/ewr/latest")
rows = data["data"]["rows"]
```

| Field | Type | Notes |
|---|---|---|
| `data.date` | string | Date of latest data |
| `data.rows` | array | One element per state |
| `data.rows[].id` | int | Database row id |
| `data.rows[].date` | string | Record date |
| `data.rows[].state` | string | State name |
| `data.rows[].total_issued_cmy` | number | Total issued (CMY) |
| `data.rows[].total_new_issued_cmy` | number | Newly issued (CMY) |
| `data.rows[].held_by_ccc_cmy` | number | Held by CCC (CMY) |
| `data.rows[].under_shipping_order` | number | Under shipping order |
| `data.rows[].open_cmy` | number | Open CMY total |
| `data.rows[].open_cmy_1` | number | Open CMY ≤1 month |
| `data.rows[].open_cmy_2` | number | Open CMY ≤2 months |
| `data.rows[].open_cmy_3_and_earlier` | number | Open CMY 3+ months |
| `data.rows[].total_open` | number | Total open contracts |
| `data.rows[].scraped_at` | string ISO datetime | When row was scraped |

---

## EWR Worksheet

**Source:** `railway:dashboard.ewrworksheet.vlmdata.com`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/ewrworksheet/latest`

Most recent EWR + shipments combined snapshot as a flat object.

```python
data = get("/v1/ewrworksheet/latest")
snapshot = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `data.id` | int | Database row id |
| `data.ewr_date` | string | EWR report date |
| `data.exports_date` | string | USDA FAS exports date |
| `data.total_issued_cmy` | number | Total issued (CMY) |
| `data.total_new_issued_cmy` | number | Newly issued (CMY) |
| `data.held_by_ccc_cmy` | number | Held by CCC (CMY) |
| `data.under_shipping_order` | number | Under shipping order |
| `data.open_cmy` | number | Open CMY total |
| `data.open_cmy_1` | number | Open CMY ≤1 month |
| `data.open_cmy_2` | number | Open CMY ≤2 months |
| `data.open_cmy_3_and_earlier` | number | Open CMY 3+ months |
| `data.total_open` | number | Total open contracts |
| `data.shipments` | number | USDA FAS weekly exports in bales |
| `data.proj_exports` | number | Projected total exports |
| `data.net_cancelled` | number | Net cancelled contracts |
| `data.sma_shipments` | number | 13-week moving average of shipments |
| `data.lagged_sma_shipments` | number | Lagged 13-week moving average |
| `data.sma_net_cancelled` | number | 13-week moving average of net cancelled |
| `data.created_at` | string ISO datetime | When row was created |
| `data.updated_at` | string ISO datetime | When row was last updated |

---

### GET `/v1/ewrworksheet/data`

Full EWR worksheet history — same fields as `/latest`, array ordered by `ewr_date` DESC.

```python
data = get("/v1/ewrworksheet/data")
history = data["data"]   # array of same-shape objects as /latest
```

| Field | Type | Notes |
|---|---|---|
| `data` | array | All historical rows — same 18 fields as `/latest` |

---

## Production Forecast

**Source:** `github:vlmsofts/production-forecast-dashboard/acres_scenarios.csv`  
**Update:** Manual (when scenario assumptions change)  
**Cache TTL:** 3600s  

---

### GET `/v1/productionforecast/scenarios`

17-row compact acreage scenarios table.

```python
data = get("/v1/productionforecast/scenarios")
rows = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `data` | array | 17 rows (one per state + US total) |
| `data[].state` | string | State abbreviation e.g. `TX` |
| `data[].state_name` | string | Full state name |
| `data[].acres_25_26_final` | string int | 2025/26 final planted acres |
| `data[].acres_ncc_26_27` | string int | NCC estimate 2026/27 |
| `data[].acres_prosp_26_27` | string int | USDA prospective plantings 2026/27 |
| `data[].acres_vlm_26_27` | string int | VLM estimate 2026/27 |
| `data[].acres_usda_june` | string int | USDA June acreage survey |

---

## Heat Units

**Source:** `railway:dashboard.heatunits.vlmdata.com`  
**Update:** Live (Railway TTL 60s)  
**Cache TTL:** 60s  

---

### GET `/v1/heatunits/stations`

Station location list — metadata for all monitored cotton belt stations.

```python
data = get("/v1/heatunits/stations")
stations = data["data"]
```

| Field | Type | Notes |
|---|---|---|
| `data` | array | Station objects |
| `data[].location` | string | Station identifier |
| `data[].city` | string | |
| `data[].state` | string | |
| `data[].lat` | number | Latitude |
| `data[].lon` | number | Longitude |

---

### GET `/v1/heatunits/latest`

Latest CHU readings per station — daily and cumulative season totals.

```python
data = get("/v1/heatunits/latest")
rows = data["data"]["rows"]
```

| Field | Type | Notes |
|---|---|---|
| `data.date` | string | Report date |
| `data.rows` | array | Station readings |
| `data.rows[].date` | string | Observation date |
| `data.rows[].location` | string | Station identifier |
| `data.rows[].city` | string | |
| `data.rows[].state` | string | |
| `data.rows[].tmax` | number | Maximum temperature °F |
| `data.rows[].tmin` | number | Minimum temperature °F |
| `data.rows[].chu` | number | Daily corn heat units |
| `data.rows[].cum_chu` | number | Cumulative CHU for season |

---

## VLM Newsletters

**Source:** Supabase — `vlm_newsletters` table  
**Update:** Weekly (when new issue is published)  
**Cache TTL:** 3600s  

---

### GET `/v1/newsletters`

Index of all 205+ newsletters — no `pdf_text`. Supports date range filtering and full-text search.

```python
# Search for newsletters mentioning "harvest"
data = get("/v1/newsletters", q="harvest")

# All newsletters in 2026
data = get("/v1/newsletters", **{"from": "2026-01-01", "to": "2026-12-31"})
```

**Query params:**

| Param | Notes |
|---|---|
| `from` | YYYY-MM-DD — filter to `issue_date >= from` |
| `to` | YYYY-MM-DD — filter to `issue_date <= to` |
| `q` | Full-text search via PostgreSQL `tsvector` on `pdf_text` |

| Field | Type | Notes |
|---|---|---|
| `count` | int | Number of results |
| `newsletters` | array | Newsletter index objects |
| `newsletters[].id` | int | |
| `newsletters[].title` | string | |
| `newsletters[].slug` | string | Use for `/v1/newsletters/{slug}` |
| `newsletters[].issue_number` | int | |
| `newsletters[].issue_date` | string YYYY-MM-DD | |
| `newsletters[].sent_at` | string ISO datetime | |
| `newsletters[].recipient_count` | int | |

---

### GET `/v1/newsletters/{slug}`

Single newsletter with full extracted PDF text. Cache TTL: 86400s (24h).

```python
data = get("/v1/newsletters/vlm-weekly-2026-06-02")
full_text = data["pdf_text"]
```

**Path params:** `slug` — from `newsletters[].slug` in the index

| Field | Type | Notes |
|---|---|---|
| `id` | int | |
| `title` | string | |
| `slug` | string | |
| `issue_number` | int | |
| `issue_date` | string YYYY-MM-DD | |
| `pdf_text` | string | Full extracted text from newsletter PDF |
| `sent_at` | string ISO datetime | |
| `recipient_count` | int | |
| `view_count` | int | |
| `created_at` | string ISO datetime | |

---

## Chart Analysis

**Source:** None — chart analysis dashboard calls Claude API directly, no data proxy.  
**Cache TTL:** N/A  

---

### GET `/v1/chartanalysis/info`

Returns static metadata. No upstream call.

```python
data = get("/v1/chartanalysis/info")
```

| Field | Type | Notes |
|---|---|---|
| `type` | string | `ai_tool` |
| `dashboard` | string | `chartanalysis` |
| `note` | string | Explanation that this dashboard uses Claude API directly |

---

## Catalog

### GET `/v1/catalog`

Machine-readable version of this document. No caching. Returns the full domain/endpoint/field structure as JSON.

```python
data = get("/v1/catalog")
domains = data["domains"]
```

| Field | Type | Notes |
|---|---|---|
| `version` | string | API version |
| `base_url` | string | `https://vlmapi.vlmdata.com` |
| `auth` | object | Auth method description |
| `domains` | array | All domain objects with endpoints and field schemas |

---

## Generic GitHub Passthrough

### GET `/v1/github/{repo}/{path}`

Reads any file from any `vlmsofts` GitHub repo. For CSV files, parses rows and returns as JSON array. For JSON files, returns parsed object. Returns `404` if file not found.

```python
# Read full oi_data.csv (large — ~15MB)
data = get("/v1/github/oi-dashboard/data/oi_data.csv")
all_rows = data["content"]

# Read spread_ohlc.csv
data = get("/v1/github/oi-dashboard/data/spread_ohlc.csv")
```

**Path params:**

| Param | Notes |
|---|---|
| `{repo}` | GitHub repo name within `vlmsofts` org e.g. `oi-dashboard` |
| `{path}` | File path within repo e.g. `data/oi_data.csv` |

| Field | Type | Notes |
|---|---|---|
| `content` | array or object | CSV → array of row objects; JSON → parsed object |

> **Performance note:** This endpoint returns the full file with no filtering. For large files use the named endpoints instead: `/v1/openinterest/daily` for filtered OI data, `/v1/spreads/{commodity}` for spread data.

---

## GEX & Options Flow (PLANNED)

> **STATUS: design spec — NOT yet implemented.** No `/v1/gex/*` or `/v1/flow/*` route exists in the gateway as of 2026-06-16. This section documents the agreed schemas so consumers can build ahead of deployment. The data is produced locally by the **options-flow-analyzer** pipeline (`gex_settle_run.py` → `gex_calculator.py`, plus the backtest/synopsis/parsed-flow tooling) and must be ingested into the gateway before these endpoints go live.

**Origin of the data (so the numbers are understood):**
- The daily terrain is the **official final ICE settle** for the prior session's **trade date** (T+1 publication convention — see the `options_oi.csv` date note above), sourced through the gateway and re-validated (`expire_dt − days_to_exp == trade_date`). It includes the **Nov serial CTX6** alongside the Dec complex.
- Greeks are Black-76 with `T = calendar_days / 365` (matches the dashboard and Bloomberg OVDV).
- All times/cadence below are the **producer** cadence; once ingested, the gateway adds the standard `cached` / `stale` envelope and the usual 300s TTL.

**Common conventions for these endpoints:**
- All under `/v1/`, all GET, all use the `X-VLM-API-Key` header.
- `net_gex` is in **contracts** unless a field name ends in `_dollars`. `net_gex_dollars = net_gex_contracts × 500` (ICE CT multiplier: 50,000 lb × $0.01).
- Spot/strike/forward values are **¢/lb**. `trade_date` is the **settle date the data represents**, not the publish/run date.
- GEX sign convention: **positive = call-heavy** (per 1¢ move).

---

### GET `/v1/gex/CT/daily`  *(planned)*

The gamma terrain for the CT December complex on one trade date — the headline daily product (walls, flip, balance, and the full net-GEX-vs-spot curve).

**Source (producer):** `data/{date}/gex_output.json` — written by `gex_settle_run.py` ~10:00 ET weekdays.

```python
data = get("/v1/gex/CT/daily", date="2026-06-15")   # specific trade date
data = get("/v1/gex/CT/daily")                        # most recent available
terrain = data["terrain"]                             # [{spot, net_gex}, ...]
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `date` | YYYY-MM-DD | Trade date T. Omit → most recent available date. |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `trade_date` | string YYYY-MM-DD | The settle date this terrain represents (not the run date). |
| `generated_at` | string ISO datetime | When the producer built the file. |
| `complex` | string | Contracts aggregated into the terrain, e.g. `CTZ6+CTU6+CTX6` (Dec + Sep/Nov serials). |
| `dte` | int | Calendar days to expiry of the front (Dec) contract. |
| `forward` | float | Front-month forward (¢/lb), put-call-parity median over the chain. |
| `call_wall` | float | Strike with the largest call-gamma concentration (upside "wall"). |
| `put_wall` | float | Strike with the largest put-gamma concentration (downside "wall"). |
| `balance` | float | Zero-crossing of the terrain — the spot where net gamma (Γ) flips sign. |
| `gamma_flip` | float | Spot level where the regime changes from put-heavy to call-heavy. |
| `net_gex_contracts` | int | Net gamma exposure at the forward, in contract-equivalents. |
| `net_gex_dollars` | int | `net_gex_contracts × 500` — $ per 1¢ move. |
| `vol_sensitivity` | int | Δ net_gex (contracts) per **+1 vol point** — structural-stability measure. |
| `convention` | string | Human-readable sign convention, e.g. `"Per-1¢ move; positive=call-heavy"`. |
| `basis_note` | string | Provenance line, e.g. official-final-settle + CTX6-included note. |
| `terrain` | array | The curve: `[{spot, net_gex}, ...]` across **±45% of the forward**. `spot` ¢/lb, `net_gex` contracts. |
| `terrain[].spot` | float | Hypothetical spot level (¢/lb). |
| `terrain[].net_gex` | int | Net GEX (contracts) at that spot. |
| `expiries_included` | array | ICE contract codes folded into the aggregate, e.g. `["CTZ6","CTU6","CTX6","CTF7",...]`. |

---

### GET `/v1/gex/CT/latest`  *(planned)*

Lightweight pointer — the most recent available trade date and where its full terrain lives, **without** the terrain array. For consumers that poll for "is there a new date?" before fetching the heavy payload.

**Source (producer):** `data/latest.json` (written by `gex_settle_run.py` after the daily run).

```python
ptr = get("/v1/gex/CT/latest")
if ptr["trade_date"] > my_last_seen:
    full = get("/v1/gex/CT/daily", date=ptr["trade_date"])
```

| Field | Type | Notes |
|---|---|---|
| `trade_date` | string YYYY-MM-DD | Most recent available trade date. |
| `generated_at` | string ISO datetime | Build time of the latest run. |
| `path` | string | Repo-relative path to that date's chart data, e.g. `data/2026-06-15/gex_chart_data.json`. |

---

### GET `/v1/gex/CT/history`  *(planned)*

The recomputed historical gamma surface across the full backtest range — **one row per trade date**. For research/backtesting the wall/flip/balance series over time.

**Source (producer):** `data/backtest/gex_backtest_daily.csv` — on-demand recompute (`gex_backtest.py`).

```python
data = get("/v1/gex/CT/history", **{"from": "2025-06-25", "to": "2026-06-15"})
rows = data["rows"]
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `from` | YYYY-MM-DD | Start date. Omit → earliest available (~2025-06-25). |
| `to` | YYYY-MM-DD | End date. Omit → most recent. |
| `fields` | string (CSV) | Subset of row fields to return. Omit → all. |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `from` | string YYYY-MM-DD | Effective start of the returned range. |
| `to` | string YYYY-MM-DD | Effective end of the returned range. |
| `count` | int | Number of rows returned. |
| `rows` | array | One object per trade date (below). |
| `rows[].trade_date` | string YYYY-MM-DD | Settle date. |
| `rows[].forward` | float | Parity-median forward (¢/lb). |
| `rows[].call_wall` | float | Largest call-gamma strike. |
| `rows[].put_wall` | float | Largest put-gamma strike. |
| `rows[].balance` | float | Net-gamma zero-crossing. |
| `rows[].net_gex_contracts` | int | Net GEX at the forward (contracts). |
| `rows[].n_strikes` | int | Strikes in that day's surface. **`< 20` = thin (early history); treat as indicative.** |
| `rows[].parity_settle_basis` | float | Parity forward − Bloomberg generic settle — a **cross-check** column, not a primary value. |

> **History caveats:** Pre-2026-06-01 settles are `PX_LAST`-derived (sub-cent off true settle) — affects only `parity_settle_basis`, not the primary parity forward. Days with `n_strikes < 20` are early/thin.

---

### GET `/v1/gex/CT/convexity`  *(planned)*

ATM-gamma evolution and σ-band convexity series — how the gamma structure tightens/loosens over time (a client-requested measure).

**Source (producer):** `data/backtest/convexity_series.csv` — on-demand recompute (`gex_backtest_analyze.py`).

```python
data = get("/v1/gex/CT/convexity", **{"from": "2025-10-22", "to": "2026-06-15"})
rows = data["rows"]
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `from` | YYYY-MM-DD | Start date. |
| `to` | YYYY-MM-DD | End date. |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `from` / `to` | string YYYY-MM-DD | Effective range. |
| `count` | int | Rows returned. |
| `rows` | array | One object per trade date (below). |
| `rows[].trade_date` | string YYYY-MM-DD | Settle date. |
| `rows[].atm_gamma` | float | Net gamma (Γ) in contracts **at the forward** (ATM). |
| `rows[].g_1sigma` | float | Net Γ at **±1σ** from the forward (σ = ATM_IV × forward × √T). |
| `rows[].g_2sigma` | float | Net Γ at **±2σ** from the forward. |
| `rows[].sigma_1_2_spread` | float | `g_1sigma − g_2sigma` — the convexity measure (how fast Γ decays 1σ→2σ). |
| `rows[].speed_peak_offset` | float | ¢ distance from the forward to the **peak-speed** point (steepest ∂NetGEX/∂S) — the "belly". |
| `rows[].forward` | float | Forward (¢/lb). |
| `rows[].balance` | float | Net-gamma zero-crossing. |

> **Coverage:** Only ~162 of ~245 historical dates carry this series — it requires the `n_strikes ≥ 20` strike gate (thin early-history dates are excluded).

---

### GET `/v1/flow/CT/parsed`  *(planned)*

Structured, parsed CT options flow from the end-of-day snapshot drops — each row is a printed trade with its parsed structure, strikes, delta hedge, and quality flags.

**Source (producer):** `drops/CT/parsed/CT_parsed_{date}_{time}.json` (per snapshot; full field reference in `drops/CT/parsed/README.md`).

```python
# Dec putspreads on 2026-06-15, grammar-valid only, ≥100 lots
data = get("/v1/flow/CT/parsed", date="2026-06-15",
           structure="putspread", month="CTZ6",
           grammar_valid="true", min_volume=100)
rows = data["rows"]
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `date` | YYYY-MM-DD | Trade date — returns all snapshots for that date. |
| `from` | YYYY-MM-DD | Start of a date range. |
| `to` | YYYY-MM-DD | End of a date range. |
| `grammar_valid` | bool | `true` → only grammar-valid rows. Omit → all. |
| `min_volume` | int | Minimum contract volume. Omit → 1 (all). |
| `structure` | string | Filter by `parsed_structure`, e.g. `callspread`, `put`, `straddle`, `putspread`. |
| `month` | string | Filter by `parsed_month`, e.g. `CTZ6`, `CTU6`. |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `date` | string YYYY-MM-DD | Echo of the requested date (when `date` used). |
| `count` | int | Rows returned. |
| `grammar_valid_count` | int | Of those, how many are grammar-valid. |
| `review_flag_count` | int | Of those, how many need human review. |
| `rows` | array | Parsed flow rows (below). |
| `rows[].snapshot_date` | string YYYY-MM-DD | Trade date of the snapshot. |
| `rows[].ex_time` | string | Exchange print time, e.g. `14:18:11 EDT`. |
| `rows[].description` | string | Raw human-readable print, e.g. `Dec26 79.00/76.00 putspread vs 76.70Δ10`. |
| `rows[].price` | float | Trade price (premium, ¢/lb). |
| `rows[].trade_type` | string | `BUY` (lifted offer), `SELL` (hit bid), `BLOCK` (blue diamond), `NEUTRAL`. |
| `rows[].aggregated_volume` | bool | `true` if the Σ symbol was present — a **summed** print. |
| `rows[].tick` | string | `UP` / `DOWN` / `SAME` — price direction vs the prior trade. |
| `rows[].volume` | int | Contracts. |
| `rows[].origin` | string | Origin/exchange code, e.g. `CO`. |
| `rows[].parsed_structure` | string | Parsed structure name, e.g. `putspread`. |
| `rows[].parsed_month` | string | Parsed underlying month, ICE code, e.g. `CTZ6`. |
| `rows[].parsed_strikes` | array(float) | Strikes in the structure, e.g. `[79.00, 76.00]`. |
| `rows[].delta_hedge` | object/null | `{price, delta}` of the futures hedge leg, or null if none. |
| `rows[].grammar_valid` | bool | `true` = parsed cleanly; filter on this for analytical use. |
| `rows[].review_flag` | bool | `true` = needs human review. |

---

### GET `/v1/gex/CT/synopsis`  *(planned)*

The weekly gamma + flow synopsis as **desk-ready plain text** (the newsletter note).

**Source (producer):** `data/weekly/synopsis_YYYY-WNN.txt` (written by `synopsis_generator.py` after Lou completes that week's `answers_YYYY-WNN.json` review).

```python
data = get("/v1/gex/CT/synopsis", week="2026-W25")
print(data["synopsis"])
```

**Query params:**

| Param | Type | Notes |
|---|---|---|
| `week` | string YYYY-WNN | ISO week. Omit → most recent available. |

**Response:**

| Field | Type | Notes |
|---|---|---|
| `week` | string YYYY-WNN | The ISO week returned. |
| `trade_dates` | array(YYYY-MM-DD) | The session dates the synopsis covers. |
| `generated_at` | string ISO datetime | When the synopsis was generated. |
| `synopsis` | string | Full four-section plain-text note: terrain, flow, fuel/brake, review queue. |

> **Availability:** Returns `404 {"error": "Synopsis not yet generated for this week"}` until Lou completes the weekly review — there is no draft/partial synopsis.

---

## Error Responses

All errors return consistent JSON. Python exceptions are never exposed.

| Status | Body | When |
|---|---|---|
| `401` | `{"error": "unauthorized", "message": "API key required"}` | Missing or wrong `X-VLM-API-Key` |
| `400` | `{"error": "invalid commodity", "valid": [...]}` | Invalid commodity code |
| `400` | `{"error": "pc must be C or P"}` | Invalid `pc` param |
| `404` | `{"error": "file not found", "path": "..."}` | GitHub file does not exist |
| `503` | `{"error": "upstream unavailable", "source": "github\|railway"}` | Upstream unreachable AND no stale cache available |
| `500` | `{"error": "internal error"}` | Unhandled exception (logged internally; not exposed) |

---

*This document describes the API as of 2026-06-16. Base URL: `https://vlmapi.vlmdata.com`. All endpoints GET only. The "GEX & Options Flow (PLANNED)" section documents design-spec endpoints not yet implemented.*
