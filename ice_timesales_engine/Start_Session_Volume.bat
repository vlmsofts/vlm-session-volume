@echo off
REM ===========================================================================
REM  Start_Session_Volume.bat  --  one-click launcher for the ICE Session-Volume
REM  dashboard. Starts the Flask engine (if not already running) and opens the
REM  browser to it. Safe to double-click repeatedly: it will NOT start a second
REM  server if one is already up on the port.
REM ===========================================================================

setlocal
cd /d "%~dp0"

set PORT=5062
set URL=http://127.0.0.1:%PORT%/

echo ==========================================================
echo   VLM  --  ICE Session-Volume dashboard
echo   folder: %CD%
echo   url:    %URL%
echo ==========================================================
echo.

REM --- Is a server already listening on the port? -------------------------
REM netstat returns 0 (found) when the port is already LISTENING.
netstat -ano | findstr /r /c:"TCP.*:%PORT% .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo Server already running on port %PORT% -- opening browser only.
    start "" "%URL%"
    goto :done
)

REM --- Not running: start it, wait for it to come up, then open browser ----
echo Starting the Flask engine in a new window...
start "VLM Session-Volume engine" cmd /k python -m api.app

echo Waiting for the server to accept connections...
REM Poll the port up to ~15s so the browser opens only once it's live.
set /a TRIES=0
:waitloop
timeout /t 1 /nobreak >nul
set /a TRIES+=1
netstat -ano | findstr /r /c:"TCP.*:%PORT% .*LISTENING" >nul 2>&1
if %errorlevel%==0 goto :ready
if %TRIES% geq 15 (
    echo.
    echo WARNING: server did not come up within 15s. Check the engine window
    echo for errors ^(e.g. missing Python packages: pip install -r requirements.txt^).
    echo Opening the browser anyway...
    goto :openbrowser
)
goto :waitloop

:ready
echo Server is up.

:openbrowser
start "" "%URL%"

:done
echo.
echo Dashboard opened at %URL%
echo ^(Close the "VLM Session-Volume engine" window to stop the server.^)
echo.
timeout /t 3 /nobreak >nul
endlocal
