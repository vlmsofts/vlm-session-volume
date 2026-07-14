# Supabase copy — COMPLETE (2026-07-07)

## Final state
All 6 engine tables migrated via `jobs/migrate_to_cloud.py --replace` and
independently verified exact against Supabase (counts + fingerprints match):
`ticks`=130,490, `bar5m`=195,188, `minute_agg`=15,249, `ingest_log`=25,
`reconcile_flags`=25, `block_supplement`=2. RLS left disabled to match the
project's other tables.

Root cause of the earlier block: NOT a Supabase platform incident — it was a
stale/incorrect database password. Once the password was reset via the
dashboard (Connect -> Session pooler -> Reset password), the Session pooler
connection authenticated immediately and the migration ran clean in one shot.
The Transaction/Direct pooler host (`db.<ref>.supabase.co`) genuinely does NOT
resolve on this IPv4-only network (AAAA-only DNS record) — that diagnosis was
correct and still applies; use the Session pooler host
(`aws-1-us-west-2.pooler.supabase.com:5432`) going forward.

**Security note:** multiple DB passwords were pasted into chat during
troubleshooting (now superseded). Rotate the password again once no longer
needed for ad-hoc access.

## Prior state (superseded, kept for history)
- Data NOT yet copied: `ticks` (target 130,490) and `bar5m` (target 195,188).
  Both were TRUNCATED to 0 on 2026-07-06 to start clean.

## Why it's paused
The ONLY correct way to bulk-copy is the direct Postgres connection
(`jobs/migrate_to_cloud.py` via `DATABASE_URL`). On 2026-07-06 that failed with
`password authentication failed for user "postgres"` through the **Session pooler**
(IPv4 host `aws-1-us-west-2.pooler.supabase.com:5432`), AND the direct/transaction
host `db.luhvqxneulzqsyltcluh.supabase.co` won't DNS-resolve on this IPv4-only
network. Supabase had an ACTIVE platform incident that day (dashboard banner
"We are investigating a technical issue") — the pooler auth failure lines up with
that. The MCP `execute_sql` tool works but CANNOT do bulk copy (no file import;
every row must be transcribed by the model — unsafe at this scale). See memory
`supabase-bulk-copy-lesson`.

## To finish (do this when Supabase incident has cleared — check dashboard banner gone)
1. Supabase dashboard → Connect → **Session pooler** → **URI** → copy string.
   If it shows `[YOUR-PASSWORD]`, click **Reset password** (a letters+numbers-only
   password avoids URL-escaping issues) and copy the full string.
2. In PowerShell (window-scoped, password never saved):
   ```powershell
   $env:DATABASE_URL = "postgresql://postgres.luhvqxneulzqsyltcluh:<PW>@aws-1-us-west-2.pooler.supabase.com:5432/postgres"
   cd "C:\Users\Louis\OneDrive - VLM Commodities LTD\Desktop\VLM_Session_Volume_Project\ice_timesales_engine"
   python -m jobs.migrate_to_cloud --replace
   ```
   `--replace` wipes the 6 engine tables and copies all fresh. It prints a
   per-table count table and says "MIGRATION COMPLETE -- all counts verified".
3. Expected final counts (also the verify fingerprints):
   - ticks 130,490  (sum seq_num=489,147,814,663 · sum price=10,316,018.74 · sum size=198,260)
   - bar5m 195,188  (sum sum_size=10,360,634 · sum trade_count=6,557,789)
   - minute_agg 15,249 · ingest_log 25 · reconcile_flags 25 · block_supplement 2

## If the pooler auth STILL fails after the incident clears
It's not the password — it's the pooler. Options: (a) run migrate from a machine/
network with IPv6 so the direct `db.<ref>.supabase.co` host resolves; (b) contact
Supabase support re: pooler auth. Do NOT try to finish via the MCP execute_sql
tool — it is the wrong tool for bulk row copy.
