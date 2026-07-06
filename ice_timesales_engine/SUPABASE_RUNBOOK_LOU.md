# SUPABASE RUNBOOK — engine cloud DB (LOU EXECUTES; nothing here runs automatically)

> Lou's rule 2026-07-05: the Supabase step is Lou-driven — explicit
> instructions, you execute each step yourself. The engine runs fully on
> local SQLite until you do this; nothing breaks by waiting. Doing this makes
> the archive queryable from Railway (cloud dashboard) instead of only this PC.

## What this does (plain terms)
Copies the engine's database (the 5-minute volume archive + tick tables) into
a NEW, engine-only Postgres schema on your existing Supabase project. It
touches nothing your other dashboards use — the engine's tables are its own
(`ticks`, `minute_agg`, `bar5m`, `ingest_log`, `reconcile_flags`,
`block_supplement`) and don't exist in Supabase today.

## Before you start — check Supabase status
2026-07-04/05: an active Supabase platform incident (capacity/restarts across
regions, tracked at https://status.supabase.com) caused password-auth
failures on a freshly-reset database password — confirmed NOT a code or
connection-string issue (psycopg parsed the URL correctly, region/host/user
all verified byte-for-byte). If Step 3 fails with "password authentication
failed" right after a reset, check status.supabase.com FIRST before
resetting again — retrying against an active incident just wastes resets.

## Step 1 — get the connection string (2 minutes, Supabase website)
1. Go to https://supabase.com/dashboard → your project.
2. Left sidebar → **Project Settings** (gear) → **Database**.
3. Under **Connection string**, pick **URI**. Copy it. It looks like:
   `postgresql://postgres:<YOUR-PASSWORD>@db.<ref>.supabase.co:5432/postgres`
4. If it shows `[YOUR-PASSWORD]`, replace that with your database password
   (the one from project creation; resettable on the same page).

## Step 2 — tell the engine about it (on this PC)
PowerShell, in the engine folder:
```powershell
$env:DATABASE_URL = "postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
```
(That sets it for THIS window only — nothing saved anywhere yet.)

## Step 3 — create the tables + copy the data (one command)
```powershell
cd "C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\VLM_Session_Volume_Project\ice_timesales_engine"
python -m jobs.migrate_to_cloud
```
(Written + guard-tested 2026-07-05: it hard-refuses to run unless
DATABASE_URL is set in the window — no default, no stored credential. If any
engine table already has rows on Supabase it ABORTS; re-run with `--replace`
to wipe the SIX ENGINE TABLES ONLY and copy fresh. Postgres driver
psycopg 3.3.4 is installed. Local SQLite is read-only to this job.)

## Step 4 — make it stick (only after Step 3 verifies)
- Local daily job: add `DATABASE_URL` to this machine's user environment
  variables (Windows: Settings → System → About → Advanced system settings →
  Environment Variables → New under "User variables").
- Railway (if/when you deploy the engine's API there): set the same
  `DATABASE_URL` in the Railway service's Variables tab (railway.toml already
  documents it).

## Step 5 — verify (paste-back check)
```powershell
python -c "from store.db import connect; db=connect(); print(db.q('SELECT source, COUNT(*), SUM(sum_size) FROM bar5m GROUP BY source')); db.close()"
```
Numbers must match the local run's seed summary. If anything looks off: unset
DATABASE_URL (`Remove-Item Env:DATABASE_URL`) and everything falls back to
local SQLite untouched.

## Safety facts
- The engine's `store/db.py` speaks both dialects already (psycopg for
  `postgres://`, SQLite otherwise) — no code changes needed for the switch.
- Nothing is deleted locally; SQLite remains the fallback.
- No other repo/dashboard reads these tables; additive to your Supabase
  project. Your existing `vlm_newsletters` etc. are untouched.
