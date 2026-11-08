@echo off
rem The browser page was retired in ticket 093; VEIL now runs only as the desktop app.
start "" wscript.exe //B "%~dp0start-desktop.vbs"
