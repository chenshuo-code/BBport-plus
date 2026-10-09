@echo off
rem Writes diag_report_<time>.txt next to this file: what Windows recorded about Bloodborne crashes and
rem graphics driver resets in the last 72 hours. Send that file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect_Crash_History.ps1" %*
echo.
echo Done. Send the diag_report_*.txt file that was created in this folder.
pause
