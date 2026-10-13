@echo off
setlocal
cd /d "%~dp0"
title Miva PDF Downloader

python --version >nul 2>nul
if errorlevel 1 (
    echo Python is not installed.
    echo Install it from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add python.exe to PATH" on the first screen of the installer.
    echo Then double-click this file again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo First-time setup. Installing Python packages, please wait...
    python -m pip install --quiet playwright
    if errorlevel 1 goto fail
)

python download_pdfs.py %*
echo.
echo Finished. Your PDFs are in the "downloads" folder next to this file.
pause
exit /b 0

:fail
echo.
echo Setup failed. Check your internet connection, then double-click this file again.
pause
exit /b 1
