@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".local-tmp" mkdir ".local-tmp"
set "TEMP=%CD%\.local-tmp"
set "TMP=%TEMP%"
if exist ".venv\Scripts\python.exe" goto install
py -3 -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1
if errorlevel 1 goto no_python
py -3 -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto failed
echo Installed. Follow README.md to add your local persona and .env, then open start.bat.
pause
exit /b 0
:no_python
echo Python 3.11+ is required. See README.md; install Python on your preferred drive.
pause
exit /b 1
:failed
echo Setup failed. Your local persona and chat data have not been replaced.
pause
exit /b 1
