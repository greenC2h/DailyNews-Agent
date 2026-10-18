@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dailynews.ps1"
if errorlevel 1 (
  echo.
  echo DailyNews 启动失败，请查看上方提示或 data\logs 日志。
  pause
)
endlocal
