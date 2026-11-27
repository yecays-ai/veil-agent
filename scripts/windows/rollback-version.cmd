@echo off
setlocal
chcp 65001 >nul
set "REPO_ROOT=%~dp0..\.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"

set "TARGET=%~1"
if "%TARGET%"=="" (
    echo Usage: rollback-version.cmd ^<commit-or-tag^> [--skip-package]
    echo.
    echo Example: rollback-version.cmd d78a8b7
    echo.
    echo This script rolls back locally and repackages VEIL Desktop.
    echo It NEVER runs git fetch or git pull, and NEVER touches data\, characters\, persona\, or .env.
    pause
    exit /b 1
)

set "EXTRA="
if /i "%~2"=="--skip-package" set "EXTRA=-SkipPackage"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\rollback_version.ps1" -RepositoryPath "%REPO_ROOT%" -TargetRef "%TARGET%" %EXTRA%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Rollback failed. Your data was not touched.
)
pause
exit /b %EXIT_CODE%
