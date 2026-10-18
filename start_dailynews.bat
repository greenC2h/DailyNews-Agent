@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dailynews.ps1"
if errorlevel 1 (
  echo.
  echo DailyNews startup failed. Check the message above or data\logs.
  pause
)
endlocal
