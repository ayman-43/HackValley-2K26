@echo off
rem Starts the whole application on one address: Nagrik (Node) with Nagrik Roads (Python) behind it at /roads/.
rem   start.cmd                 -> http://localhost:5501
rem   set HOST=0.0.0.0 ^& start.cmd   -> also reachable from phones on the same Wi-Fi (live scanning)
setlocal
cd /d "%~dp0"

set "PY=%ROADS_PYTHON%"
if "%PY%"=="" if exist "%~dp0roads\.venv\Scripts\python.exe" set "PY=%~dp0roads\.venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"
if "%PORT%"=="" set "PORT=5501"

echo Starting Nagrik Roads (detection, map data, live scan) on http://127.0.0.1:8000 ...
start "Nagrik Roads API" /D "%~dp0roads" "%PY%" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

rem give the detector a few seconds to load, then open the browser
start "" cmd /c "timeout /t 6 >nul & start http://localhost:%PORT%/"

echo Starting Nagrik on http://localhost:%PORT%  (Nagrik Roads at /roads/)  - close this window to stop.
node backend\server.mjs
