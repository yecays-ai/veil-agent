@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.bat first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" veil_agent.py %*
if errorlevel 1 pause
