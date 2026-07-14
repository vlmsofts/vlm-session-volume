@echo off
REM ============================================================
REM  ICE TIMESALES ENGINE -- DAILY INGEST (ALL COMMODITIES)
REM  Writes DIRECTLY to Supabase (DATABASE_URL must be set on this
REM  user account -- see SUPABASE_COPY_RESUME.md). Runs after all
REM  three ICE EOD capture tasks land (Softs 13:35, Cotton 14:22,
REM  Surface 17:00 ET) so the day's blotter files already exist.
REM  Each commodity is independent -- one failing does not skip
REM  the rest. Never hard-fails the scheduled task (always exit 0)
REM  so Task Scheduler doesn't mark it as a chronic failure; check
REM  run_daily_ingest_all.log for per-commodity status.
REM ============================================================
cd /d "%~dp0"

if "%DATABASE_URL%"=="" (
    echo [ERR] DATABASE_URL is not set for this user -- refusing to run
    echo       ^(would silently fall back to local SQLite^). See
    echo       SUPABASE_COPY_RESUME.md to set it via setx.
    exit /b 1
)

echo ============================================================
echo   DAILY INGEST -- ALL COMMODITIES  %DATE% %TIME%
echo ============================================================

for %%C in (CT KC SB CC) do (
    echo.
    echo --- %%C ---
    py -3.14 -m jobs.daily_ingest --commodity %%C
    if errorlevel 1 echo [WARN] %%C daily_ingest returned non-zero
)

echo.
echo ============================================================
echo   DAILY INGEST DONE  %DATE% %TIME%
echo ============================================================
exit /b 0
