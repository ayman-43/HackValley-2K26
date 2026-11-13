@echo off
rem Starts Nagrik + Nagrik Roads together and opens http://localhost:5501 . Keep this window open while presenting.
rem For phones on the same Wi-Fi:  set HOST=0.0.0.0  then run start.cmd
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo [start] PROBLEM: Node.js is not installed or not on PATH. Install Node 20+ from https://nodejs.org
  pause
  exit /b 1
)
node start.mjs
echo.
echo [start] The servers have stopped. Read any message above, then press a key to close this window.
pause >nul
