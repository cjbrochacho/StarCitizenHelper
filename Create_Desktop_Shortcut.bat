@echo off
setlocal
cd /d "%~dp0"
title Star Citizen Helper - Create Desktop Shortcut

echo.
echo   Star Citizen Helper - desktop shortcut
echo   ======================================
echo.
echo   This creates a shortcut on your desktop pointing at this folder.
echo   A shortcut stores full paths, so it cannot be shipped with the
echo   project - it has to be made on the machine that will use it.
echo.

rem -- Locate Python, the same way the launcher does --------------------------
set "PY_CMD="
py -3 --version >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD (
    python --version >nul 2>&1 && set "PY_CMD=python"
)

if not defined PY_CMD (
    echo   [ERROR] Python was not found on this system, or is not in your PATH.
    echo.
    echo   Run_StarCitizenHelper.bat will walk you through installing it.
    echo   Once Python is working, run this again.
    echo.
    pause
    exit /b 1
)

%PY_CMD% sc_shortcut.py
if errorlevel 1 (
    echo.
    echo   [ERROR] The shortcut could not be created. See the message above.
    echo.
    pause
    exit /b 1
)

echo.
echo   Done. Double-click "Star Citizen Helper" on your desktop to launch.
echo.
pause
