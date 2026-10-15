@echo off
REM Elite Swing Trader - Run all tests
REM Run from project root (c:\swing-trader) so absolute imports resolve
REM Includes: auto-execution, trailing stop execution, integration, exit monitoring,
REM screening_log, ml_learning (backfill, missed_opportunity, suggestions, run_pipeline, scheduler learning), etc.
REM All tests use mocks - no real Coinbase/CCXT calls.
REM Env: `uv sync --all-groups` first (see README). Optional: `uv run pytest tests\ ...`

cd /d "%~dp0"

REM Use project venv
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Optional smoke first (lower memory than full suite):
REM   run_quick_lane.ps1
REM Lighter full run (skip stress-marked modules):
REM   python -m pytest tests\ -v --tb=short -m "not stress" %*

REM Run all tests (add --cov for coverage report)
python -m pytest tests\ -v --tb=short %*

REM To run with coverage: run_tests.bat --cov=. --cov-report=term-missing
exit /b %ERRORLEVEL%
