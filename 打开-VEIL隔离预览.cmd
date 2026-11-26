@echo off
setlocal
cd /d "%~dp0"
set "ELECTRON_RUN_AS_NODE="
set "VEIL_DESKTOP_DATA="
set "VEIL_AGENT_ROOT="
set "VEIL_DESKTOP_TEST=1"
set "VEIL_WORKSPACE_PREVIEW=1"
set "VEIL_MCP_PREVIEW="
set "ELECTRON=desktop\node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" set "ELECTRON=..\..\veil-agent\desktop\node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" set "ELECTRON=E:\Peter-Agent\VEIL\veil-agent\desktop\node_modules\electron\dist\electron.exe"
if not exist "%ELECTRON%" (
  echo Electron not found. Install desktop development dependencies inside this worktree first.
  pause
  exit /b 1
)
"%ELECTRON%" desktop\tests\smoke.cjs
if errorlevel 1 pause
