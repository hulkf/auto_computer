@echo off
setlocal

rem Double-click entrypoint for the local automation platform.
set "PROJECT_ROOT=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\start_gateway.ps1" -OpenConsole

if errorlevel 1 (
    echo.
    echo Project startup failed. Check logs\gateway.stderr.log for details.
    echo.
    pause
)
