@echo off
setlocal
cd /d "%~dp0desktop"
where node >nul 2>nul
if errorlevel 1 (
  echo Please install Node.js 24 or newer first.
  pause
  exit /b 1
)
call npm ci --no-audit --no-fund
if errorlevel 1 (
  pause
  exit /b 1
)
node node_modules/electron/install.js
if errorlevel 1 (
  pause
  exit /b 1
)
echo Ready. Double-click start-desktop.bat in the Agent folder.
pause
