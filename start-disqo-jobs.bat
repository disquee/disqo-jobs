@echo off
REM Double-click this file to start disqo jobs on Windows.
cd /d "%~dp0"

where py >nul 2>&1 && set PY=py -3 || set PY=python
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Python 3.10 or newer is required. Install it from python.org, then run this again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo.
  echo   First run - setting up. This takes a couple of minutes.
  %PY% -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat || goto :fail

python -c "import jobpilot" >nul 2>&1
if errorlevel 1 (
  echo   Installing disqo jobs...
  python -m pip install --quiet --upgrade pip >nul 2>&1
  python -m pip install --quiet -e . || goto :fail
)

echo.
echo   Starting disqo jobs at http://127.0.0.1:8000
echo   Leave this window open while you use it. Close it to quit.
start "" http://127.0.0.1:8000
REM Through the CLI, not uvicorn directly: serve reads the phone-access setting
REM and prints the on-your-phone address when it's switched on.
python -m jobpilot.cli serve --port 8000
goto :eof

:fail
echo.
echo   Setup failed. Check your internet connection and try again.
pause
exit /b 1
