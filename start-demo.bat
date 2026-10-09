@echo off
rem CivicSignal - one-click demo (Windows).
rem Uses the captured SerpApi responses in backend\fixtures\serpapi: no API key needed.
rem First run installs the Python and frontend dependencies (a few minutes).

setlocal
cd /d "%~dp0"
title CivicSignal demo launcher

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during installation, then run this file again.
  pause
  exit /b 1
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo Node.js was not found. Install Node.js 18 or newer from https://nodejs.org/
  echo then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Setting up Python environment ^(first run only^)...
  python -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :failed
)
if not exist "frontend\node_modules" (
  echo [2/2] Installing frontend packages ^(first run only^)...
  pushd frontend
  call npm.cmd install --no-audit --no-fund || (popd & goto :failed)
  popd
)

echo Starting the CivicSignal backend (captured SerpApi data) on port 8000...
start "CivicSignal backend - close to stop" /d "%~dp0backend" cmd /k "set MOCK_SERPAPI=true&& ..\.venv\Scripts\python.exe -m uvicorn civicsignal.api.app:create_app --factory --port 8000"

rem give the backend a few seconds to start before the page loads
"%SystemRoot%\System32\timeout.exe" /t 5 /nobreak >nul

echo Starting the dashboard; your browser will open automatically...
start "CivicSignal frontend - close to stop" /d "%~dp0frontend" cmd /k "npm.cmd run dev -- --open"

echo.
echo CivicSignal is starting in two new windows. Close both windows to stop it.
echo If the browser does not open, go to the address shown in the frontend window
echo (usually http://localhost:5173) and click one of the demo questions.
"%SystemRoot%\System32\timeout.exe" /t 10 >nul
exit /b 0

:failed
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
