@echo off
REM ============================================================
REM  NLC Financial Dashboard - web launcher
REM  Double-click to start the dashboard and open it in your browser.
REM
REM  On success this window closes and the server keeps running silently in
REM  the background. If anything goes wrong the window STAYS OPEN with the
REM  reason -- the old version launched straight into a windowless process,
REM  so a missing dependency or a broken file looked like "nothing happened".
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "APP=%~dp0src\webapp.py"
set "REQ=%~dp0requirements.txt"
set "WP=%LOCALAPPDATA%\WP\WPy64-313130\python"
set "PY="
set "PYW="

REM ---- 1. Find an interpreter -------------------------------------------
REM  Prefer the portable WinPython this project ships with; otherwise fall
REM  back to whatever Python is on PATH, then to the "py" launcher.
if exist "%WP%\python.exe" (
    set "PY=%WP%\python.exe"
    if exist "%WP%\pythonw.exe" set "PYW=%WP%\pythonw.exe"
    goto :found
)
where python.exe >nul 2>&1
if not errorlevel 1 (
    set "PY=python.exe"
    where pythonw.exe >nul 2>&1 && set "PYW=pythonw.exe"
    goto :found
)
where py.exe >nul 2>&1
if not errorlevel 1 (
    set "PY=py.exe"
    where pyw.exe >nul 2>&1 && set "PYW=pyw.exe"
    goto :found
)
goto :nopython

:found
echo Using Python: %PY%
echo.

REM ---- 2. Make sure every dependency is present -------------------------
REM  webapp.py hard-imports all five of these at module level, so a machine
REM  missing any one of them dies on startup. They are checked together so a
REM  single pip run fixes a brand-new machine. (The old launcher only ever
REM  installed flask and waitress, which is why a fresh clone failed on
REM  "import pandas" with no message anywhere.)
"%PY%" -c "import flask, waitress, pandas, matplotlib, openpyxl" 2>nul
if errorlevel 1 (
    echo First run on this machine - installing dependencies from requirements.txt.
    echo This takes a few minutes, and only happens once.
    echo.
    "%PY%" -m pip install --disable-pip-version-check -r "%REQ%"
    if errorlevel 1 goto :pipfailed
    echo.
    "%PY%" -c "import flask, waitress, pandas, matplotlib, openpyxl" 2>nul
    if errorlevel 1 goto :pipfailed
    echo Dependencies installed.
    echo.
)

REM ---- 3. Pre-flight: import the app with a console attached ------------
REM  The server runs windowless, which has no console for a traceback. Import
REM  the app here first so a syntax error, bad path or unreadable database
REM  reports itself on screen instead of vanishing. The path is passed as an
REM  argument, not pasted into the -c source: this folder's name contains an
REM  apostrophe ("Sarah's Excels") that would break a quoted literal.
"%PY%" -c "import sys; sys.path.insert(0, sys.argv[1]); import webapp" "%~dp0src"
if errorlevel 1 goto :appfailed

REM ---- 4. Launch -------------------------------------------------------
echo Starting the dashboard - your browser will open in a moment.
echo If it does not, go to  http://127.0.0.1:5000
if defined PYW (
    start "" "%PYW%" "%APP%"
) else (
    start "" "%PY%" "%APP%"
)
exit /b 0

:nopython
echo.
echo  ERROR: no Python interpreter was found on this computer.
echo.
echo  The dashboard needs Python 3.11 or newer. If you cannot install Python
echo  (many managed work PCs block the installer), use portable WinPython
echo  instead - it only unpacks files and needs no admin rights:
echo.
echo      https://github.com/winpython/winpython/releases
echo.
echo  Download a "dot" release, run it, and extract it so that this file exists:
echo      %WP%\python.exe
echo.
pause
exit /b 1

:pipfailed
echo.
echo  ERROR: the dependencies could not be installed.
echo.
echo  Check that this machine can reach the Python package index, then run this
echo  by hand to see the full error:
echo      "%PY%" -m pip install -r "%REQ%"
echo.
pause
exit /b 1

:appfailed
echo.
echo  ERROR: the dashboard could not start. The traceback is just above.
echo.
echo  A log is also written to:
echo      %LOCALAPPDATA%\KarbonPendingDashboard\app.log
echo.
pause
exit /b 1
