# NOTICE — Juneteenth Federal Holiday (2026-06-19)

**Date:** Friday, June 19, 2026  
**Status:** US Federal Holiday — ICE cotton/futures markets CLOSED, no live trading session.

## What runs tomorrow

| Process | Runs? | Notes |
|---|---|---|
| `session_volume.py` (overnight/day RVOL) | ❌ NO | No live trading session, no tape data to capture. Window measurements will be unavailable. |
| `check_capture.py` (capture validation) | ❌ NO | No live tape flow from ICE. Capture universe unchanged. |
| `futures_session_volume.py` (futures vol) | ⚠️ OPTIONAL | Can run manually for backfill if needed, but no live session data available. |
| `refresh_seed.py` (seed data backfill) | ✅ YES | Can run if backfill is needed; no dependency on live trading. |

**Null or empty outputs expected today.** If a scheduled task runs and returns zero results, that is expected behaviour — no live session occurred.

## Data handling

The session volume reporter expects `Options_flow_analyzer/data/<date>/ct_options_tape.csv` as its tape source.
On a market holiday, that tape will not exist or will be stale.
Scripts reading from it should guard against missing/empty tapes and log clearly rather than failing.

Normal operations resume Monday, June 22, 2026.
