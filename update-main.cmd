@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "UPDATE_EXIT=1"
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=%CD%"

where git >nul 2>&1
if errorlevel 1 (
    echo Git was not found. Please install Git for Windows.
    goto end
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo This folder is not an accessible Git repository.
    goto end
)

for /f "delims=" %%B in ('git branch --show-current') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH (
    echo The current Git branch could not be determined. Update stopped.
    goto end
)

echo Checking GitHub connection...
git fetch origin main
if errorlevel 1 (
    echo Could not reach GitHub. No local edits were saved or changed.
    echo Start your network or proxy, then run this updater again.
    goto end
)

set "STATE_FILE=%TEMP%\veil-update-main-%RANDOM%-%RANDOM%.tmp"
git status --porcelain --untracked-files=all -- . ":(exclude).worktrees/**" > "%STATE_FILE%"
if errorlevel 1 (
    del "%STATE_FILE%" >nul 2>&1
    echo Unable to check local files. Update stopped.
    goto end
)
set "HAS_LOCAL_CHANGES="
for %%Z in ("%STATE_FILE%") do if %%~zZ GTR 0 set "HAS_LOCAL_CHANGES=1"
del "%STATE_FILE%" >nul 2>&1
if defined HAS_LOCAL_CHANGES (
    echo Saving local edits and untracked files before updating...
    git stash push --include-untracked --message "Update Main backup" -- . ":(exclude).worktrees/**"
    if errorlevel 1 (
        echo Backup failed. Update stopped.
        goto end
    )
    echo Local edits are saved in Git stash. They will not overwrite the new version.
)

if /i not "%CURRENT_BRANCH%"=="main" (
    echo Switching to main. Your current branch and its commits will be kept.
    git show-ref --verify --quiet refs/heads/main
    if errorlevel 1 (
        git switch --track -c main origin/main
    ) else (
        git switch main
    )
    if errorlevel 1 (
        echo Could not switch to main. Any saved local edits remain in Git stash.
        goto end
    )
)

echo Updating main...
git merge --ff-only origin/main >nul 2>&1
if not errorlevel 1 goto update_complete

echo Local main has commits that are not on GitHub. Creating a recovery branch...
for /f "delims=" %%S in ('git rev-parse --short^=12 main') do set "LOCAL_MAIN_SHORT=%%S"
if not defined LOCAL_MAIN_SHORT (
    echo Could not identify local main. Update stopped without changing it.
    goto return_to_branch
)
set "BACKUP_BRANCH=backup/main-before-update-%LOCAL_MAIN_SHORT%"
git show-ref --verify --quiet "refs/heads/%BACKUP_BRANCH%"
if not errorlevel 1 goto backup_ready
git branch "%BACKUP_BRANCH%" main
if errorlevel 1 (
    echo Could not create the recovery branch. Update stopped without changing main.
    goto return_to_branch
)

:backup_ready
echo Saved the old local main as %BACKUP_BRANCH%.
set "COLLISION_FILE=%TEMP%\veil-update-collisions-%RANDOM%-%RANDOM%.tmp"
echo Checking only files newly tracked by GitHub...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-main-collisions.ps1" -RepositoryPath "%CD%" -BaseRef main -TargetRef origin/main >"%COLLISION_FILE%"
set "COLLISION_SCAN_EXIT=%ERRORLEVEL%"
if not "%COLLISION_SCAN_EXIT%"=="0" (
    del "%COLLISION_FILE%" >nul 2>&1
    echo Unable to check local file collisions. Update stopped; the recovery branch is preserved.
    goto return_to_branch
)
for %%Z in ("%COLLISION_FILE%") do if %%~zZ GTR 0 (
    echo Update stopped because GitHub now tracks these ignored local files:
    type "%COLLISION_FILE%"
    del "%COLLISION_FILE%" >nul 2>&1
    echo Move or rename them, then run the updater again. No local file was overwritten.
    goto return_to_branch
)
git reset --hard origin/main
if errorlevel 1 (
    echo Could not align main with GitHub. The recovery branch is preserved.
    goto return_to_branch
)

:update_complete
tasklist /FI "IMAGENAME eq VEIL.exe" /NH | findstr /I /C:"VEIL.exe" >nul
if not errorlevel 1 (
    echo Desktop is still running. Fully exit VEIL Desktop, then run this updater again to package it.
    set "UPDATE_EXIT=1"
    goto end
)
if not exist "desktop\node_modules\.bin\electron-packager.cmd" (
    echo Desktop packaging dependencies are missing. Run setup-desktop.bat, then run this updater again.
    set "UPDATE_EXIT=1"
    goto end
)
echo Packaging VEIL Desktop from the updated main source...
pushd desktop
call npm run package
set "PACKAGE_EXIT=%ERRORLEVEL%"
popd
if not "%PACKAGE_EXIT%"=="0" (
    echo The source update completed, but desktop packaging failed. Fix the packaging error and run this updater again.
    set "UPDATE_EXIT=1"
    goto end
)
set "UPDATE_EXIT=0"
echo.
echo Update and desktop packaging completed successfully:
git log -1 --oneline
echo Close Agent using the desktop menu, then open Start Desktop again.
echo This folder will stay on main so Peter starts from the version just updated.
goto end

:return_to_branch
if /i not "%CURRENT_BRANCH%"=="main" (
    echo Returning to %CURRENT_BRANCH%...
    git switch "%CURRENT_BRANCH%"
    if errorlevel 1 (
        echo Could not return automatically. Your original branch and stash are preserved.
        set "UPDATE_EXIT=1"
    )
)

:end
echo.
pause
exit /b %UPDATE_EXIT%
