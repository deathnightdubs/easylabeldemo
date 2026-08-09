@echo off
REM Double-click to start the collection manager on Windows.
REM Expects the one-time setup from the README to have been done.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run the one-time setup first:
  echo.
  echo     py -3.12 -m venv .venv
  echo     .venv\Scripts\pip install -e ".[dev]"
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m numis.ui %*
if errorlevel 1 pause
