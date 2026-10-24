@echo off
rem Ticket 108: one double-click updates the code AND the desktop app (VEIL.exe).
rem It never switches branches, never stashes, never touches data\ or persona\.
rem If anything is off it stops and says why; nothing is half-changed before git succeeds.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=%CD%"
if not exist ".local-tmp" mkdir ".local-tmp"
set "TEMP=%CD%\.local-tmp"
set "TMP=%TEMP%"
set "EXE=desktop\dist\VEIL-win32-x64\VEIL.exe"
set "STAMP=desktop\dist\built-from.txt"

echo ==== VEIL update ====
tasklist /FI "IMAGENAME eq VEIL.exe" 2>nul | find /I "VEIL.exe" >nul
if not errorlevel 1 goto still_running
rem The chat service holds data\session.lock while it runs, even with no window open.
if exist ".venv\Scripts\python.exe" if exist "data\session.lock" (
  ".venv\Scripts\python.exe" -c "import msvcrt,sys; h=open(r'data\session.lock','a+b'); h.seek(0); msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)" >nul 2>&1
  if errorlevel 1 goto still_running
)

where git >nul 2>&1
if errorlevel 1 goto no_git
set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if /i not "%BRANCH%"=="main" goto not_main

for /f "delims=" %%H in ('git rev-parse HEAD') do set "OLD=%%H"
echo [1/4] Getting the newest code...
git pull --ff-only origin main
if errorlevel 1 goto pull_failed
for /f "delims=" %%H in ('git rev-parse HEAD') do set "NEW=%%H"
if "%OLD%"=="%NEW%" (echo       Code was already up to date.) else (echo       Code updated.)

echo [2/4] Checking Python components...
if not exist ".venv\Scripts\python.exe" goto no_venv
".venv\Scripts\python.exe" -m pip install -q --no-cache-dir -r requirements.lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_memory.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" setup_fetch.py
if errorlevel 1 goto failed

echo [3/4] Checking desktop components...
where npm >nul 2>&1
if errorlevel 1 goto no_node
set "DESKTOP_DEPS_CHANGED="
if not "%OLD%"=="%NEW%" for /f "delims=" %%F in ('git diff --name-only %OLD% %NEW% -- desktop/package-lock.json') do set "DESKTOP_DEPS_CHANGED=1"
if not exist "desktop\node_modules\electron" set "DESKTOP_DEPS_CHANGED=1"
if defined DESKTOP_DEPS_CHANGED (
  pushd desktop
  call npm ci --no-audit --no-fund
  if errorlevel 1 (popd & goto failed)
  node node_modules/electron/install.js
  if errorlevel 1 (popd & goto failed)
  popd
)

echo [4/4] Rebuilding the desktop app if its code changed...
set "DESKTOP_REV="
for /f "delims=" %%R in ('git rev-list -1 HEAD -- desktop') do set "DESKTOP_REV=%%R"
set "BUILT_REV="
if exist "%STAMP%" set /p BUILT_REV=<"%STAMP%"
if not exist "%EXE%" set "BUILT_REV="
if /i "%BUILT_REV%"=="%DESKTOP_REV%" (
  echo       Desktop app is already current.
  goto done
)
pushd desktop
call npm run package
if errorlevel 1 (popd & goto failed)
popd
if not exist "%EXE%" goto failed
>"%STAMP%" echo %DESKTOP_REV%
echo       Desktop app rebuilt.

:done
echo.
echo Update finished. Opening Peter...
start "" "%EXE%"
timeout /t 3 >nul
exit /b 0

:still_running
echo Peter is still running. Quit Peter first, then double-click update.bat again.
echo Nothing was changed.
goto stop
:no_git
echo Git was not found. Please install Git for Windows. Nothing was changed.
goto stop
:not_main
echo This folder is on branch "%BRANCH%", not main. Nothing was changed.
echo Ask Claude before switching branches here.
goto stop
:pull_failed
echo Could not get the new code: no network, or files here were edited locally.
echo Git's message above says which. Nothing was changed.
goto stop
:no_venv
echo Python components are missing. Run setup.bat once, then run update.bat again.
goto stop
:no_node
echo Node.js was not found, so the desktop app could not be rebuilt. Install Node.js LTS.
goto stop
:failed
echo Update stopped at the step above. The code is updated; your chats, memory and persona were not touched.
echo Send a screenshot of this window to Claude.
goto stop
:stop
pause
exit /b 1
