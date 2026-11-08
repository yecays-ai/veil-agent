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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update-desktop-shortcut.ps1" -RepositoryPath "%~dp0."
if errorlevel 1 echo Warning: the old Start Desktop shortcut could not be updated; use start-desktop.vbs directly.
echo Ready. Double-click start-desktop.vbs in the Agent folder.
pause
