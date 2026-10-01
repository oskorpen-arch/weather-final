@echo off
title Mood Weather Exhibition - IARC 425
cd /d "%~dp0"

echo =================================================================
echo  Mood Weather Exhibition - Starting Environment Simulation...
echo =================================================================
echo.

:: Check dependencies and install if missing
echo Checking Python dependencies...
python -m pip install -r requirements.txt --quiet

:: Open browser after a short delay for Numba warm-up
echo Opening http://127.0.0.1:5000 in your browser...
start "" "http://127.0.0.1:5000"

:: Start the Python simulation backend
python app.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Simulation stopped or failed to launch.
    pause
)
