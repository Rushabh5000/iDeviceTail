@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === iDeviceTail: clearing ports 3016 3017 45455 ===
for %%P in (3016 3017 45455) do (
  for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":%%P " ^| findstr LISTENING') do taskkill /F /PID %%A >nul 2>&1
)

set "VENV_PY=.venv\Scripts\python.exe"

rem ---- is the venv already usable? then skip straight to running ----
if not exist "%VENV_PY%" goto :setup
"%VENV_PY%" -c "import idevicetail, aiohttp, zeroconf, aiosqlite" >nul 2>&1
if not errorlevel 1 (
  echo === venv ready - skipping install ===
  goto :run
)

:setup
echo === Setting up virtualenv - one time ===
rem pymobiledevice3 deps have no wheels for the newest CPython; prefer 3.13/3.12
set "PYEXE="
for %%V in (3.13 3.12 3.11) do if not defined PYEXE (
  py -%%V -c "import sys" >nul 2>&1 && set "PYEXE=py -%%V"
)
if not defined PYEXE set "PYEXE=python"
echo Using interpreter: !PYEXE!
if not exist "%VENV_PY%" !PYEXE! -m venv .venv
if not exist "%VENV_PY%" goto :err
"%VENV_PY%" -m pip install --upgrade pip >nul
echo === Installing dependencies - pulls in pymobiledevice3 for Engine A ===
"%VENV_PY%" -m pip install -e ".[device]"
if not errorlevel 1 goto :run
echo.
echo WARNING: full install failed - retrying without Engine A. App-log agent still works.
echo          For system logs use a Python 3.12 or 3.13 venv - see docs/GUIDE.md
"%VENV_PY%" -m pip install -e "."
if errorlevel 1 goto :err

:run
echo === Starting server on http://localhost:3017 ===
start "iDeviceTail" cmd /k "%VENV_PY%" -m idevicetail serve --host 0.0.0.0 --port 3017 --agent-port 45455
exit /b 0

:err
echo.
echo Setup failed. See messages above. You need Python 3.10+ on PATH.
pause
exit /b 1
