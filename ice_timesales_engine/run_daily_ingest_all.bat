@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  ICE TIMESALES ENGINE -- DAILY INGEST (ALL COMMODITIES)
REM
REM  Writes DIRECTLY to Supabase (DATABASE_URL must be set on this
REM  user account -- see SUPABASE_COPY_RESUME.md).
REM
REM  RUNS TWICE A DAY [2026-08-24]:
REM    15:00 ET  primary. Blotters land 14:22-14:25 (median 14:25)
REM              and the futures settle ~14:41, so 15:00 clears both
REM              with margin and puts the session on the dashboard
REM              ~2h10m earlier than the old single 17:10 run.
REM    17:10 ET  backstop, unchanged. Keeps its ~2h45m margin and its
REM              zero-missed-run record; catches a late or recovered
REM              capture that 15:00 was too early for.
REM
REM  SAFE TO RUN TWICE -- by construction, not by luck:
REM    * upsert_ticks is ON CONFLICT DO NOTHING on
REM      (commodity, session_date, ice_code, seq_num), so re-reading
REM      a file inserts 0 rows rather than duplicating.
REM    * the sha256 skip only fires on an IDENTICAL file, so a file
REM      that GREW since 15:00 is re-read in full at 17:10.
REM    * minute_agg and bar5m are delete-and-reinsert per day.
REM  A partial 15:00 read followed by a complete 17:10 read is safe.
REM
REM  NO ICE / COM CONTENTION: the ingest reads FILES only. Verified
REM  across all 19 repo modules it imports -- no win32com, no
REM  Dispatch, no ice.get_*. The single network call is a Cloudflare
REM  edge purge, which is unconfigured on this box (immediate no-op)
REM  and is timeout-capped and exception-guarded when it is not.
REM  So 15:00 costs the shared ICE COM session nothing, even though
REM  it falls inside the 13:35-15:30 contention window.
REM
REM  KNOWN AND DELIBERATE: at 15:00 the futures settle exists but
REM  settled_surface does not land until ~15:32. Futures volume --
REM  everything this ingest computes -- is unaffected. The 15:00 run
REM  is FUTURES-COMPLETE and OPTIONS-PARTIAL. See RUNBOOK_INGEST.md.
REM
REM  Each commodity is independent: one failing never skips the rest.
REM  Per-commodity status is logged AND the exit code is real, so a
REM  silent failure is now visible in Task Scheduler's Last Result.
REM ============================================================
cd /d "%~dp0"

set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\run_daily_ingest_all.log"

REM --- TRADING-CALENDAR GATE: skip weekends/holidays --------------------
REM  daily_ingest guards holidays too, but it tests the SESSION date it is
REM  about to process, not TODAY -- and with --date omitted it takes the
REM  latest day-folder on disk. Without this gate a Saturday run would
REM  re-process Friday: a no-op thanks to the sha256 skip, but pointless
REM  work and log noise. Mirrors the three ICE capture tasks.
py -3.14 is_trading_day.py
if errorlevel 1 (
    call :log "[SKIP] Not a trading day -- no ingest today."
    endlocal & exit /b 0
)

if "%DATABASE_URL%"=="" (
    call :log "[ERR] DATABASE_URL is not set for this user -- refusing to run"
    call :log "      It would silently fall back to local SQLite. See SUPABASE_COPY_RESUME.md."
    endlocal & exit /b 1
)

call :log "============================================================"
call :log "  DAILY INGEST -- ALL COMMODITIES  %DATE% %TIME%"
call :log "============================================================"

set /a FAILED=0
set "FAILLIST="

for %%C in (CT KC SB CC) do (
    call :log ""
    call :log "--- %%C ---"
    REM Tee: the log keeps the full per-commodity output, and the console
    REM still shows it for an interactive run.
    py -3.14 -m jobs.daily_ingest --commodity %%C >> "%LOG%" 2>&1
    if errorlevel 1 (
        set /a FAILED+=1
        set "FAILLIST=!FAILLIST! %%C"
        call :log "[FAIL] %%C daily_ingest returned non-zero -- see the output above"
    ) else (
        call :log "[ OK ] %%C"
    )
)

call :log ""
if %FAILED% GTR 0 (
    call :log "============================================================"
    call :log "  DAILY INGEST FINISHED WITH %FAILED% FAILED COMMODITY/IES:!FAILLIST!"
    call :log "  %DATE% %TIME%"
    call :log "============================================================"
    REM REAL exit code. Previously this was always 0, so a commodity could
    REM fail every day and Task Scheduler would still report success.
    endlocal & exit /b 1
)

call :log "============================================================"
call :log "  DAILY INGEST DONE -- all 4 commodities OK  %DATE% %TIME%"
call :log "============================================================"
endlocal & exit /b 0

:log
REM Write one line to BOTH the console and the log. An empty argument must
REM print a blank line, not cmd's "ECHO is off." banner -- hence echo(.
if "%~1"=="" (
    echo(
    echo(>> "%LOG%"
) else (
    echo %~1
    echo %~1>> "%LOG%"
)
exit /b 0
