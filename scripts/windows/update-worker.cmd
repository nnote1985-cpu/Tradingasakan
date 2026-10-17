@echo off
rem Double-click to update the Testnet worker: pull the latest main, then restart it.
rem Same as: git pull --ff-only origin main, then scripts\windows\restart-worker.ps1
rem Keys, the clock and the scheduled task are not touched here.
cd /d "%~dp0..\.."
echo Updating %CD% ...
git pull --ff-only origin main
if errorlevel 1 (
  echo.
  echo git pull failed: the worker was NOT restarted. Check for local changes with: git status
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-worker.ps1"
echo.
echo Done. Check the Worker tab on the web app in about 20 seconds.
pause
