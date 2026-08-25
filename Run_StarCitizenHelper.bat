@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo =========================================
echo  Star Citizen Helper v1.2  --  Launcher
echo =========================================
echo.

:: ── Locate Python ─────────────────────────────────────────────────────────────
::
::  Try in order:
::    1. py.exe  (Python Launcher — bundled with official Python for Windows)
::    2. python  (direct, works for Microsoft Store installs and manual PATH setups)

set PY_CMD=

where py >nul 2>&1
if !errorlevel! == 0 (
    py -3 --version >nul 2>&1
    if !errorlevel! == 0 (
        set PY_CMD=py -3
        goto :python_found
    )
)

where python >nul 2>&1
if !errorlevel! == 0 (
    python --version >nul 2>&1
    if !errorlevel! == 0 (
        set PY_CMD=python
        goto :python_found
    )
)

:: ── Python not found ───────────────────────────────────────────────────────────
echo [ERROR] Python was not found on this system, or is not in your PATH.
echo.
echo   How to install Python:
echo.
echo   Option 1 ^(Recommended^) -- Microsoft Store:
echo     1. Open the Start menu and search for "Microsoft Store"
echo     2. Search for "Python 3" inside the Store
echo     3. Install the version published by Python Software Foundation
echo     This automatically adds Python to your PATH -- no extra steps.
echo.
echo   Option 2 -- python.org installer:
echo     1. Visit  https://www.python.org/downloads/
echo     2. Download the latest Python 3 Windows installer
echo     3. Run it and TICK "Add Python to PATH" before clicking Install Now
echo.
echo   If you already installed Python but still see this message:
echo     - Open Start, search "Edit the system environment variables"
echo     - Click Environment Variables, find "Path" under User variables
echo     - Make sure the Python install folder is listed (e.g. C:\Python312\)
echo     - Click OK, then close and re-open this launcher
echo.
pause
exit /b 1

:python_found
set /p _dummy=<nul
%PY_CMD% --version
echo [OK] Python found.
echo.

:: ── Require Python 3.8+ ───────────────────────────────────────────────────────
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.8 or newer is required.
    echo         Please update Python using one of the options above.
    echo.
    pause
    exit /b 1
)

:: ── Install / verify dependencies ─────────────────────────────────────────────
echo Installing / verifying dependencies...
echo.
%PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo.
    echo   Possible causes:
    echo     - No internet connection
    echo     - pip needs an upgrade:  py -3 -m pip install --upgrade pip
    echo     - Permission error: right-click this file and choose
    echo       "Run as administrator", then try again
    echo.
    pause
    exit /b 1
)
echo.

:: ── Launch ────────────────────────────────────────────────────────────────────
echo Starting Star Citizen Helper...
echo.
%PY_CMD% StarCitizenHelperv1.2.py
if errorlevel 1 (
    echo.
    echo [ERROR] The app exited with an error. See the details above.
    pause
)
