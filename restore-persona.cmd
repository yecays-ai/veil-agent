@echo off
rem Ticket 214: double-click to pick an earlier saved persona and put it back.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore-persona.ps1"
echo.
pause
