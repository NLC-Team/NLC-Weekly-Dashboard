@echo off
REM ============================================================
REM  Karbon Pending Dashboard - launcher
REM  Double-click this file to open the dashboard from source.
REM  It auto-finds a Python that has the required libraries.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "APP=%~dp0src\main.py"
set "WP=%LOCALAPPDATA%\WP\WPy64-313130\python"

REM 1) Portable WinPython that ships with pandas/matplotlib (most reliable)
if exist "%WP%\python.exe" (
  "%WP%\python.exe" -c "import pandas, matplotlib" 1>nul 2>nul
  if !errorlevel! equ 0 (
    start "" "%WP%\pythonw.exe" "%APP%"
    exit /b 0
  )
)

REM 2) "python" on your PATH
python -c "import pandas, matplotlib" 1>nul 2>nul
if !errorlevel! equ 0 (
  start "" pythonw "%APP%"
  exit /b 0
)

REM 3) the "py" launcher on your PATH
py -c "import pandas, matplotlib" 1>nul 2>nul
if !errorlevel! equ 0 (
  start "" pyw "%APP%"
  exit /b 0
)

echo.
echo Could not find a Python that has the required libraries (pandas, matplotlib).
echo.
echo  - Easiest: double-click  dist\KarbonDashboard.exe  (no Python needed)
echo  - Or install the libraries into your Python:
echo        python -m pip install pandas matplotlib
echo.
pause
exit /b 1
