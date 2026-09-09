@echo off
setlocal enabledelayedexpansion
echo === iDeviceTail: stopping ports 3016 3017 45455 ===
for %%P in (3016 3017 45455) do (
  for /f "tokens=5" %%A in ('netstat -aon ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /PID %%A >nul 2>&1 && echo   killed PID %%A on port %%P
  )
)
echo done.
