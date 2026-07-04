# ice_timesales_engine

Daily-collected analytics over the ICE futures **time & sales tick tape**
(EOD blotter files in `C:\Ice eod records\` — READ-ONLY source). Slice traded
volume by **any time block × contract month × aggregate × trade type**
(outright / spread-leg / EFS / EFP / block), query it in milliseconds, view it
in a Flask/Plotly dashboard.

CT (Cotton) live; KC/CC/SB parametrized (month-sets in `commodity_meta.py` —
note SB has no December).

## Quick start (local)

```powershell
pip install -r requirements.txt
python -m pytest tests/                                  # 45 tests
python -m jobs.backfill --commodity CT                   # ingest all on-disk days
python -m api.app                                        # UI + API on :5061
```

Open http://127.0.0.1:5061 — pick date, window (night/day/full/custom),
contracts, trade types; totals, profile chart, reconcile flags.

## Daily job (after the EOD capture, ~16:15 ET)

```powershell
python -m jobs.daily_ingest --commodity CT
```

Idempotent (sha256 file-skip + tick PK), holiday-aware (clean exit), a
no-blotter day is a zero-volume day, not a failure.

## API

Base `/v1/sessionvol` (all GET):

| Endpoint | Example |
|---|---|
| `/{cmd}/window` | `?date=2026-07-02&preset=night&contracts=CTZ6&types=outright_ask,outright_bid` |
| `/{cmd}/window` (custom) | `?date=2026-07-02&start=09:15&end=10:30` |
| `/{cmd}/profile` | `?date=2026-07-02&preset=full&bucket=60m` |
| `/{cmd}/contracts` | `?date=2026-07-02` |
| `/{cmd}/reconcile` | `?date=2026-07-02` |
| `/catalog` | — |

`contracts` accepts ice codes (`CTZ6`), generic codes (`CTDEC1`), or `all`.
Every response carries a freshness envelope; reconcile labels are
informational (`expected_gap` / `suspect_capture` / `no_settle`) — the settle
file is a cross-check, never ground truth.

## Deployment (pending — needs env vars)

- `DATABASE_URL` — postgres URL (Supabase) for prod; unset = local SQLite.
- `CF_ZONE_ID` / `CF_API_TOKEN` — Cloudflare cache-tag purge (no-op unset).
- Railway: `railway.toml` provided; serve via
  `gunicorn 'api.app:create_app()'`.

## Layout

`ingest/` parse→normalize→classify→load→aggregate · `store/` schema + dual-dialect
db + query repo · `api/` Flask API + window resolution + cache headers ·
`ui/` dashboard · `jobs/` daily_ingest + backfill · `tests/` incl. the locked
2026-07-02 acceptance fixture. Decisions: `MEMORY.md`. Pitfalls: `ERRORS.md`.
