@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Star Citizen Helper - Setup

echo.
echo   ================================================
echo    Star Citizen Helper - Setup
echo   ================================================
echo.
echo   This will:
echo     1. Check for Python, and install it if it is missing
echo     2. Install the packages the app needs
echo     3. Put a shortcut on your desktop
echo.
echo   Safe to run again at any time.
echo   ------------------------------------------------
echo.

rem ── 1. Python ─────────────────────────────────────────────────────────────
echo   [1/3] Looking for Python...
call :find_python
if defined PY_CMD goto :python_found

echo         Not installed. Setting it up for you now.
echo.
call :install_python
echo.
echo         Checking again...
call :find_python

if not defined PY_CMD (
    echo.
    echo   ------------------------------------------------
    echo   [ERROR] Python could not be installed automatically.
    echo   ------------------------------------------------
    echo.
    echo   Install it by hand from the page opening now. During setup, tick
    echo   "Add python.exe to PATH", then run this installer again.
    echo.
    start "" https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:python_found
echo         Found !PY_VERSION!
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] Python 3.8 or newer is required, but !PY_VERSION! was found.
    echo   Install a newer version from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
%PY_CMD% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] This Python has no tkinter, so the window cannot be drawn.
    echo   Reinstall Python from python.org and leave "tcl/tk and IDLE" ticked.
    echo.
    pause
    exit /b 1
)
echo.

rem ── 2. Packages ───────────────────────────────────────────────────────────
echo   [2/3] Installing packages...
%PY_CMD% -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 (
    echo         Retrying for this user account only...
    %PY_CMD% -m pip install --disable-pip-version-check --quiet --user -r requirements.txt
)
if errorlevel 1 (
    echo.
    echo   [ERROR] The packages could not be installed. Check your internet
    echo   connection and run this again.
    echo.
    pause
    exit /b 1
)
echo         Done.
echo.

rem ── 3. Shortcut ───────────────────────────────────────────────────────────
echo   [3/3] Creating the desktop shortcut...
%PY_CMD% sc_shortcut.py
if errorlevel 1 (
    echo.
    echo   [WARNING] The shortcut could not be created, but the app is ready.
    echo   Start it with Run_StarCitizenHelper.bat
    echo.
    pause
    exit /b 0
)

echo.
echo   ------------------------------------------------
echo    Setup complete.
echo   ------------------------------------------------
echo.
echo   Launch it from the "Star Citizen Helper" shortcut on your desktop.
echo.

choice /C YN /N /M "  Launch it now? [Y/N] "
if errorlevel 2 goto :finished
if errorlevel 1 start "" "%~dp0Run_StarCitizenHelper.bat"

:finished
echo.
exit /b 0


rem ══════════════════════════════════════════════════════════════════════════
rem  Locate a usable Python.
rem  Sets PY_CMD (quoted when it is a full path, so spaces survive) and
rem  PY_VERSION. Leaves PY_CMD empty when nothing usable is present.
rem ══════════════════════════════════════════════════════════════════════════
:find_python
set "PY_CMD="
set "PY_VERSION="

rem The py launcher is the most reliable entry point when it exists.
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :got_python
)

rem "python" on PATH may be the Microsoft Store stub, which opens the Store
rem instead of reporting a version - so only trust it if it answers properly.
set "PY_RAW="
for /f "delims=" %%V in ('python --version 2^>^&1') do set "PY_RAW=%%V"
echo !PY_RAW! | findstr /b /c:"Python 3" >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :got_python
)

rem Straight after an install, PATH in this window is still stale, so look
rem where Python actually lands - per-user first, then machine-wide.
for %%R in (
    "%LOCALAPPDATA%\Programs\Python"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
    "C:\"
) do (
    for /d %%D in ("%%~R\Python3*") do (
        if exist "%%~D\python.exe" (
            set PY_CMD="%%~D\python.exe"
            goto :got_python
        )
    )
)
exit /b 0

:got_python
for /f "delims=" %%V in ('%PY_CMD% --version 2^>^&1') do set "PY_VERSION=%%V"
exit /b 0


rem ══════════════════════════════════════════════════════════════════════════
rem  Install Python: winget first, then a direct download from python.org.
rem  Both install per-user, so neither needs administrator rights.
rem ══════════════════════════════════════════════════════════════════════════
:install_python
where winget >nul 2>&1
if errorlevel 1 (
    echo         winget is not available here, going straight to python.org.
    goto :download_python
)

for %%I in (Python.Python.3.13 Python.Python.3.12 Python.Python.3.11) do (
    echo         Installing %%I with winget...
    winget install --exact --id %%I --scope user --silent ^
        --accept-source-agreements --accept-package-agreements
    call :find_python
    if defined PY_CMD exit /b 0
)

echo         winget could not install it. Falling back to python.org...

:download_python
set "PY_VER=3.12.10"
set "ARCH=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "ARCH=%PROCESSOR_ARCHITEW6432%"
set "SUFFIX=-amd64"
if /i "!ARCH!"=="ARM64" set "SUFFIX=-arm64"
if /i "!ARCH!"=="x86"   set "SUFFIX="
set "PY_FILE=python-%PY_VER%!SUFFIX!.exe"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/!PY_FILE!"
set "PY_TMP=%TEMP%\!PY_FILE!"

echo         Downloading !PY_FILE! ...
powershell -NoProfile -Command ^
    "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri '!PY_URL!' -OutFile '!PY_TMP!' } catch { exit 1 }"
if errorlevel 1 (
    echo         The download failed.
    exit /b 1
)

echo         Running the Python installer. This can take a minute...
"!PY_TMP!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del "!PY_TMP!" >nul 2>&1
exit /b 0
