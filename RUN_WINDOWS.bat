@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Telegram Channel Downloader v3.4
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo First-time setup: running INSTALL.bat...
    call "%~dp0INSTALL.bat"
    exit /b
)
"%~dp0.venv\Scripts\python.exe" -B "%~dp0telegram_channel_downloader.py"
if errorlevel 1 (
    echo.
    echo The downloader exited with an error.
    pause
    exit /b 1
)
exit /b 0
