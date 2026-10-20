@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.bat first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" web_launcher.py %*

