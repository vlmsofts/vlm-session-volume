# Phase 3c — VLM Data Gateway endpoints for session-volume

Phase 3a/3b are verified (overnight 546 matches the old history exactly; both jobs live). This
spec defines the API layer. Build it to match the gateway's **already-agreed PLANNED conventions**
(see `VLM_API_REFERENCE.md` → "GEX & Options Flow (PLANNED)"). Do not invent a new style.

---

## 0. WHERE THE WORK HAPPENS — two surfaces, both with zero-collision discipline

Phase 3c spans two codebases. Be explicit about which change goes where.

1. **Producer (the new `vlm_session_volume` repo):** make the data API-ready and publish it to a
   GitHub source the gateway can read. Additive only.
2. **Gateway (the VLM Data Gateway service, `vlmapi` — a SEPARATE production codebase):** add new
   route handlers under the flow namespace. **This is an existing production service — the same
   zero-collision rule applies:** add only new route files/handlers, reuse the existing
   `github_reader` + standard-envelope plumbing, modify NO existing route, and confirm zero existing
   files changed. Follow the existing `routes/*.py` module pattern (e.g. `routes/ice.py`,
   `routes/signals.py`).

> The gateway reads source files from GitHub via `github_reader.read_file()`, caches 300s, and
> falls back to stale cache. So the flow is: producer writes files → pushes to a GitHub source repo
> → gateway routes read them and wrap in the standard envelope. No new infrastructure.

---

## 1. Inherited conventions (apply to every route)
- Path under `/v1/`, **GET only**, `X-VLM-API-Key` header required.
- Standard response envelope adds `cached` / `stale` / `stale_age_seconds`. 300s cache TTL.
- **Units:** volume in **contracts**. `session_date` is the **ET session/trade date the data
  represents**, not the run date. Strikes/prices ¢/lb if ever included.
- **Capture universe is enforced upstream** (producer): only Dec/Mar/May/Jul, 1st & 2nd generic
  (8 slots); October and August already excluded. The API never returns excluded months.
- **Canonical contract key** in every per-contract object: `generic_code` (CTDEC1) + `ice_code`
  (CTZ6) + `month_code` (Z) + `month_name` (Dec) + `delivery_year` (4-digit, 2026).
- **Error table (reuse verbatim):** `401 unauthorized`; `400 invalid commodity {valid:[...]}`;
  `404 file not found {path}`; `503 upstream unavailable {source}`; `500 internal error`. Python
  exceptions never exposed. A holiday / no-session date returns `404 {"error":"no session","date":...}`.

---

## 2. Source files the PRODUCER must publish (to the GitHub source repo)

| File | Drives | Notes |
|---|---|---|
| `data/{date}/session_volume.json` | `/session-volume/daily` | Full single-session payload (both windows, 8-slot per-contract, RVOL tiers). Shape MUST equal §3.1. |
| `data/latest_session_volume.json` | `/session-volume/latest` | Lightweight pointer `{session_date, generated_at, path}`. Written each EOD run. |
| `data/history/session_volume_history.csv` | `/session-volume/history` (session-level) | PERMANENT, one row per (session_date), both windows' totals + call/put. |
| `data/history/session_volume_by_contract.csv` | `/session-volume/history?by=contract` | **PERMANENT, one row per (session_date, generic_code)** — REQUIRED for December-across-years (see §4). Was "optional" in the build authorization; the owner's long-timeline requirement makes it mandatory. |

All four publish to the agreed GitHub source repo (see Open Question #1). Producer is additive; the
permanent CSVs are append-only/idempotent-by-key as already specified in the build authorization.

---

## 3. Endpoints (gex-aligned)

> **Refinement vs the build authorization:** the authorization sketched `latest` = full latest
> session and `/{date}` = single date. To match the house style (gex uses `/daily?date=` for the
> full payload and `/latest` as a lightweight pointer), this spec uses **`/daily`** for the full
> single-session payload (omit `date` → most recent) and **`/latest`** as the pointer. Net
> capability is identical; only the naming aligns to the gateway. Flag if you'd rather keep the
> `/{date}` path form.

### 3.1 GET `/v1/flow/{commodity}/session-volume/daily`  *(new)*
Full session payload for one session date — the headline product.
**Source:** `data/{date}/session_volume.json`.
```python
data = get("/v1/flow/CT/session-volume/daily", date="2026-06-17")  # specific session
data = get("/v1/flow/CT/session-volume/daily")                      # most recent available
```
**Query params:** `date` YYYY-MM-DD (omit → most recent available).
**Response:**
| Field | Type | Notes |
|---|---|---|
| `commodity` | string | `CT`. |
| `session_date` | YYYY-MM-DD | ET session date the data represents. |
| `generated_at` | ISO datetime | Producer build time. |
| `windows` | object | Keys `overnight` and `day` (below). |
| `windows.{w}.window` | string | e.g. `"21:00→07:00 ET"` / `"07:00→14:20 ET"`. |
| `windows.{w}.snapshot_ts` | string | Tape timestamp the window total was read at. |
| `windows.{w}.total` / `.call` / `.put` | int | Contracts. |
| `windows.{w}.pc_ratio` | float\|null | Put/Call. |
| `windows.{w}.rvol` | object | Keyed `"5"/"10"/"20"/"30"/"60"`; each `{lookback, avg, rvol, flag, available, have, need}`. `available:false` + `rvol:null` + `have/need` when insufficient history (graceful degradation). `flag` ∈ `HIGH`(≥2)/`LOW`(≤0.5)/`null`. |
| `windows.{w}.by_contract` | array | 8-slot breakdown, each `{generic_code, ice_code, month_code, month_name, delivery_year, total, call, put}`. |
| `excluded` | array | Informational, e.g. `["October (CTV)","August (CTQ)"]`. |
| `convention` | string | e.g. `"Volume in contracts; session_date is the ET session date; Oct/Aug excluded; 8-slot universe."` |

### 3.2 GET `/v1/flow/{commodity}/session-volume/latest`  *(new)*
Lightweight pointer (no heavy arrays) — for polling "is there a new session?".
**Source:** `data/latest_session_volume.json`.
| Field | Type | Notes |
|---|---|---|
| `session_date` | YYYY-MM-DD | Most recent available session. |
| `generated_at` | ISO datetime | Build time. |
| `path` | string | Repo-relative path to that session's `session_volume.json`. |

### 3.3 GET `/v1/flow/{commodity}/session-volume/history`  *(new)*
Trailing record for long-timeline comparison — one row per session (session-level) or per
(session, contract) when `by=contract`.
**Source:** `session_volume_history.csv` (session-level) / `session_volume_by_contract.csv` (`by=contract`).
```python
# Session-level, both windows, last quarter
data = get("/v1/flow/CT/session-volume/history", **{"from":"2026-03-01","to":"2026-06-17"})
# December (DEC1) overnight volume across years — the owner's example
data = get("/v1/flow/CT/session-volume/history",
           by="contract", generic="CTDEC1", window="overnight",
           **{"from":"2023-01-01","to":"2026-12-31"})
```
**Query params:**
| Param | Type | Notes |
|---|---|---|
| `from` / `to` | YYYY-MM-DD | Date range. Omit → earliest / latest available. |
| `window` | string | `overnight` \| `day` \| `both` (default `both`). |
| `by` | string | `session` (default) or `contract` (switches to the per-contract series). |
| `generic` | string | When `by=contract`: filter to a generic, e.g. `CTDEC1`. |
| `month_code` | string | When `by=contract`: filter by month, e.g. `Z` (all Dec generics). |
| `fields` | string (CSV) | Subset of row fields. Omit → all. |

**Response:**
| Field | Type | Notes |
|---|---|---|
| `from` / `to` | YYYY-MM-DD | Effective range. |
| `count` | int | Rows returned. |
| `window` | string | Echo of the window filter. |
| `by` | string | `session` or `contract`. |
| `rows` | array | Session-level: `{session_date, overnight_total/call/put, day_total/call/put, rvol_* per tier}`. Contract-level (`by=contract`): `{session_date, generic_code, ice_code, month_code, month_name, delivery_year, window, total, call, put}`. |

---

## 4. Worked example — "how does December compare over the last 3 years"
1. `GET /v1/flow/CT/session-volume/history?by=contract&generic=CTDEC1&window=both&from=2023-01-01&to=2026-12-31`
2. Each row carries `delivery_year` and the resolved `ice_code`, so the consumer aligns the
   front-December (DEC1) overnight/day volume year over year directly.
3. To cross-reference OI/settle, join on `generic_code` to `/v1/openinterest/CT` (`bbg_ticker
   CTDEC1 Comdty`, history to 2008). This is exactly why the per-contract permanent history (§2,
   row 4) is mandatory — without it, the API cannot answer this at the contract level.

---

## 4b. Additional dataset — deep generic futures volume/OI history (its own route)
The **Bloomberg-seeded deep generic series** (front-Dec/Jul/etc. daily volume + OI, years back —
sourced via `cotton_futures_volume_history_blpapi.py`, NOT oi_data) is broadly useful on its own, so it
gets its **own** family route rather than only living inside the session-volume payload:
`GET /v1/futures/{commodity}/volume-history?generic=CTDEC1&from=&to=&fields=` — standard envelope,
`github_reader` source, same `X-VLM-API-Key`. This is the direct answer to "December volume over the
last N years" and lets other API-family consumers use the series independently. Same later-phase +
additive-routes-only + separate-prod-codebase discipline as §3.

---

## 5. Gated build order + confirmations
- **3c-i (producer):** emit `latest_session_volume.json`, ensure `session_volume.json` matches §3.1,
  add the **per-contract permanent history**; backfill it from the session-level history + any tapes
  still present. Publish all four files to the GitHub source repo. Test shapes against §3 with a
  local fixture. **Stop and show the owner a sample payload before touching the gateway.**
- **3c-ii (gateway):** add the three routes in a new `routes/flow_session_volume.py` (or matching the
  service's module convention), reusing `github_reader` + the standard envelope + the error table.
  Modify no existing route. Add route tests mirroring the gateway's existing test style.
- **Confirm at the end:** (a) zero existing files modified in BOTH repos; (b) each route returns the
  standard envelope and the §3 schema; (c) the error table behaves (401/400/404/503/500); (d) the
  December-across-years query in §4 returns correct rows; (e) exact paths of every new file + route.

---

## 6. OPEN QUESTIONS — confirm before building
1. **GitHub source repo + gateway repo:** which GitHub repo should the producer publish session-volume
   data to (a new `vlmsofts/session-volume`, or a path inside an existing dashboard repo like
   `oi-dashboard`)? And confirm the gateway service repo/location where `routes/*.py` live, so routes
   can be added there. (The analyzer needs read/commit access to both.)
2. **Endpoint naming:** keep the house-style `/daily` + `/latest` split (§3, recommended), or retain
   the authorization's `/{date}` path form?
3. **Auth/secrets:** the producer publishing to GitHub and the gateway reading it both need
   credentials (GitHub token; `X-VLM-API-Key` issuance). Confirm these are already provisioned or
   need setting up — and keep them in env/secrets, never committed.
