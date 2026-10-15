@echo off
REM Swing Trader — Daily Health Check
REM Run by Windows Task Scheduler at 7:00 AM daily.
REM Logs to data\health_check.log (appended each run).

cd /d "%~dp0.."
echo [%date% %time%] Starting health check >> data\health_check.log 2>&1
".venv\Scripts\python.exe" scripts\run_health_check.py >> data\health_check.log 2>&1
echo [%date% %time%] Health check done (exit %ERRORLEVEL%) >> data\health_check.log 2>&1
