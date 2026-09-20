@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  ICE TIMESALES ENGINE -- CATCH-UP INGEST (SECOND PASS)
REM
REM  Runs jobs.catchup_ingest over the last 5 trading days and
REM  ingests any (commodity, date) whose blotter files are ON DISK
REM  but which has NO bar5m rows in the database.
REM
REM  WHY THIS EXISTS [2026-09-20]:
REM    The daily ingest fires on a fixed clock (15:00 / 17:10 ET).
REM    The ICE softs capture fires at 17:00 and loops KC -> SB -> CC
REM    sequentially, so the tail of that loop can still be writing
REM    when 17:10 comes round. On 2026-09-18 CC finished at 17:29 and
REM    SB needed a manual rerun that landed at 20:13; both were
REM    skipped and nothing went back for them, so CC and SB lost the
REM    whole session. Moving the 17:10 clock does not fix it -- the
REM    next slow capture moves past the new time too. A later pass
REM    that asks "what is on disk but not in the DB?" does.
REM
REM  Sibling of run_daily_ingest_all.bat: same DATABASE_URL guard,
REM  same py -3.14 pin, same logs\<name>.log destination.
REM
REM  NO TRADING-CALENDAR GATE, DELIBERATELY. run_daily_ingest_all.bat
REM  needs one because it omits --date and would re-process Friday on
REM  a Saturday. This job takes an explicit window and its selection
REM  rule (jobs/catchup_ingest.py::select_catchup_days) already skips
REM  closed dates AND any day with zero blotter files -- so a weekend
REM  run is a clean no-op that reports "nothing missing", and gating
REM  it out would suppress exactly the Friday-evening catch-up this
REM  exists to perform.
REM
REM  SAFE TO RUN REPEATEDLY -- by construction, not by luck:
REM    * it only selects commodity-days with NO bar5m rows, so once a
REM      day is ingested it is never selected again (anti-spin).
REM    * a day with zero blotter files is never selected, whatever the
REM      DB says -- that is what stops it looping forever on a genuine
REM      zero-volume or holiday-adjacent day (e.g. 2026-07-03).
REM    * the ingest it calls is itself idempotent: upsert_ticks is
REM      ON CONFLICT DO NOTHING and minute_agg/bar5m are
REM      delete-and-reinsert per day.
REM
REM  Exits with jobs.catchup_ingest's own exit code.
REM ============================================================
cd /d "%~dp0"

set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\run_catchup_ingest.log"

if "%DATABASE_URL%"=="" (
    call :log "[ERR] DATABASE_URL is not set for this user -- refusing to run"
    call :log "      It would silently fall back to local SQLite. See SUPABASE_COPY_RESUME.md."
    endlocal & exit /b 1
)

call :log "============================================================"
call :log "  CATCH-UP INGEST  %DATE% %TIME%"
call :log "============================================================"

REM Tee: the log keeps the full output, and the console still shows it
REM for an interactive run.
py -3.14 -m jobs.catchup_ingest --days 5 >> "%LOG%" 2>&1
set "RC=!ERRORLEVEL!"

if not "!RC!"=="0" (
    call :log "[FAIL] catchup_ingest returned !RC! -- see the output above"
) else (
    call :log "[ OK ] catchup_ingest"
)
call :log "  %DATE% %TIME%"
call :log "============================================================"

REM Real exit code, propagated: a silent green is the whole failure
REM mode this job was built to end.
endlocal & exit /b %RC%

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
