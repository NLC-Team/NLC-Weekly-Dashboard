@echo off
REM ============================================================
REM  NLC Financial Dashboard - web launcher
REM  Double-click to open the dashboard in your browser.
REM  The server runs silently in the background.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "APP=%~dp0src\webapp.py"
set "PY=%LOCALAPPDATA%\WP\WPy64-313130\python\python.exe"
set "PYW=%LOCALAPPDATA%\WP\WPy64-313130\python\pythonw.exe"

if not exist "%PY%" set "PY=python"
if not exist "%PYW%" set "PYW=pythonw"

REM Install dependencies silently using python.exe (has output)
"%PY%" -c "import flask" 2>nul
if !errorlevel! neq 0 (
    echo Installing Flask...
    "%PY%" -m pip install flask --quiet
)
"%PY%" -c "import waitress" 2>nul
if !errorlevel! neq 0 (
    echo Installing Waitress...
    "%PY%" -m pip install waitress --quiet
)

REM Launch with pythonw.exe — no console window, runs in background
REM The dashboard opens your browser automatically
start "" "%PYW%" "%APP%"
exit /b 0
