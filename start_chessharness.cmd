@echo off
setlocal
title ChessHarness

cd /d "%~dp0"

set "APP_URL=http://localhost:5173"
set "STOCKFISH_DIR=%LOCALAPPDATA%\Programs\Stockfish\18"

rem Make the recommended Stockfish install visible even to an older Explorer
rem process that has not picked up the latest user PATH yet.
if exist "%STOCKFISH_DIR%\stockfish.exe" set "PATH=%STOCKFISH_DIR%;%PATH%"

if not exist "config.yaml" goto missing_config

where uv.exe >nul 2>&1
if errorlevel 1 goto missing_uv

where npm.cmd >nul 2>&1
if errorlevel 1 goto missing_node

if not exist "frontend\node_modules\.bin\vite.cmd" (
    echo [ChessHarness] Installing frontend dependencies...
    pushd "frontend"
    call npm.cmd install
    if errorlevel 1 (
        popd
        goto frontend_failed
    )
    popd
)

echo [ChessHarness] Starting the backend and frontend...
echo [ChessHarness] Open %APP_URL% if the browser does not open automatically.
echo [ChessHarness] Press Ctrl+C to stop both servers.
echo.

if not defined CHESSHARNESS_NO_BROWSER (
    start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process '%APP_URL%'"
)

uv run python scripts\dev.py
set "APP_EXIT=%ERRORLEVEL%"
if "%APP_EXIT%"=="0" exit /b 0
if "%APP_EXIT%"=="130" exit /b 0

echo.
echo [ChessHarness] The app stopped with exit code %APP_EXIT%.
pause
exit /b %APP_EXIT%

:missing_config
echo [ChessHarness] config.yaml is missing.
echo Copy config.example.yaml to config.yaml and add your provider credentials.
pause
exit /b 1

:missing_uv
echo [ChessHarness] uv was not found on PATH.
echo Install uv from https://docs.astral.sh/uv/getting-started/installation/
pause
exit /b 1

:missing_node
echo [ChessHarness] npm was not found on PATH.
echo Install the current Node.js LTS release from https://nodejs.org/
pause
exit /b 1

:frontend_failed
echo.
echo [ChessHarness] Frontend dependency installation failed.
pause
exit /b 1
