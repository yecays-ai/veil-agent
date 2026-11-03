@echo off
rem Ticket 212: one double-click removes the voice-rules block from persona\peter.md (saves a copy first).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0undo-voice-rules.ps1"
echo.
pause
