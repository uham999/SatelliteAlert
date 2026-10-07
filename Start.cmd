@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo Run Install.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" satellite_alert.py %*
set "satellite_exit=%errorlevel%"
if not "%satellite_exit%"=="0" pause
exit /b %satellite_exit%
