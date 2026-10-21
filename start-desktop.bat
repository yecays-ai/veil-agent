@echo off
setlocal
cd /d "%~dp0desktop"
if exist "dist\VEIL-win32-x64\VEIL.exe" (
  start "" "dist\VEIL-win32-x64\VEIL.exe"
  exit /b 0
)
if not exist "node_modules\electron\dist\electron.exe" (
  echo Please run setup-desktop.bat first.
  pause
  exit /b 1
)
start "" "node_modules\electron\dist\electron.exe" .
