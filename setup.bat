@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".local-tmp" mkdir ".local-tmp"
set "TEMP=%CD%\.local-tmp"
set "TMP=%TEMP%"
where node >nul 2>&1
if errorlevel 1 goto no_node
where npx >nul 2>&1
if errorlevel 1 goto no_node
goto node_checked
:no_node
echo Node.js LTS with npm/npx not found. Peter will chat normally without file tools; install Node.js LTS to enable them.
:node_checked
if exist ".venv\Scripts\python.exe" goto check_existing_python
py -3 -c "import sys; assert (3, 11) <= sys.version_info[:2] < (3, 13)" >nul 2>&1
if errorlevel 1 goto no_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:check_existing_python
".venv\Scripts\python.exe" -c "import sys; assert (3, 11) <= sys.version_info[:2] < (3, 13)" >nul 2>&1
if errorlevel 1 goto no_python
:install
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.lock.txt
if errorlevel 1 goto failed
echo Installing the pinned Nowhere runtime in an isolated Python environment...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-nowhere.ps1" -RepositoryPath "%CD%"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_memory.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_fetch.py
if errorlevel 1 goto failed
echo Installed. Follow README.md to add your local persona and .env, then open start.bat.
pause
exit /b 0
:no_python
echo Python 3.11 or 3.12 is required. See README.md; install Python on your preferred drive.
pause
exit /b 1
:failed
echo Setup failed. Your local persona and chat data have not been replaced.
pause
exit /b 1
