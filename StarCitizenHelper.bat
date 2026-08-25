@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Star Citizen Helper

rem Everything below is a check first and an action only if needed, so a normal
rem launch costs a fraction of a second. First run does the whole setup.

rem ── Python ────────────────────────────────────────────────────────────────
call :find_python
if defined PY_CMD goto :python_ready

echo.
echo   Setting up Star Citizen Helper for the first time.
echo   Python is not installed - fetching it now.
echo.
call :install_python
call :find_python

if not defined PY_CMD (
    echo.
    echo   [ERROR] Python could not be installed automatically.
    echo.
    echo   Install it by hand from the page opening now, tick
    echo   "Add python.exe to PATH", then run this again.
    echo.
    start "" https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:python_ready
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] Python 3.8 or newer is required, but !PY_VERSION! was found.
    echo   Get a newer one from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
%PY_CMD% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] This Python has no tkinter, so the window cannot be drawn.
    echo   Reinstall from python.org and leave "tcl/tk and IDLE" ticked.
    echo.
    pause
    exit /b 1
)

rem ── Package ───────────────────────────────────────────────────────────────
%PY_CMD% -c "import keyboard" >nul 2>&1
if errorlevel 1 (
    echo   Installing the keyboard package...
    %PY_CMD% -m pip install --disable-pip-version-check --quiet keyboard
    if errorlevel 1 %PY_CMD% -m pip install --disable-pip-version-check --quiet --user keyboard
    %PY_CMD% -c "import keyboard" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   [ERROR] The keyboard package could not be installed.
        echo   Check your internet connection and try again.
        echo.
        pause
        exit /b 1
    )
)

rem ── Icon and desktop shortcut ─────────────────────────────────────────────
rem Both are generated rather than shipped, because a .lnk stores absolute
rem paths and an icon is a build artefact. Made once, then left alone.
if not exist "%~dp0assets\StarCitizenHelper.ico" (
    echo   Drawing the icon...
    %PY_CMD% -m helper.shortcut --icon-only >nul 2>&1
)

rem A marker rather than looking for the .lnk itself: finding the real desktop
rem means asking PowerShell, which costs more than every other check combined,
rem and a shortcut you deleted on purpose should stay deleted.
if not exist "%~dp0assets\.shortcut-made" (
    echo   Creating a desktop shortcut...
    %PY_CMD% -m helper.shortcut >nul 2>&1
    if not errorlevel 1 echo made> "%~dp0assets\.shortcut-made"
)

rem ── Launch ────────────────────────────────────────────────────────────────
rem Everything above has already checked that the app can run, so hand off to
rem the windowed interpreter and exit. Staying attached would leave this
rem console in the taskbar alongside the app for the whole session.
call :find_pythonw
start "" %PYW_CMD% "%~dp0StarCitizenHelper.py"
exit /b 0


rem ══════════════════════════════════════════════════════════════════════════
rem  Locate a usable Python.
rem  Sets PY_CMD (quoted when it is a full path, so spaces survive) and
rem  PY_VERSION. Leaves PY_CMD empty when nothing usable is present.
rem ══════════════════════════════════════════════════════════════════════════
:find_python
set "PY_CMD="
set "PY_VERSION="

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
rem  The same interpreter, windowed - pythonw rather than python - so running
rem  the app does not open a console. Falls back to the console one if the
rem  windowed build is missing, which is better than not starting at all.
rem ══════════════════════════════════════════════════════════════════════════
:find_pythonw
rem Ask the interpreter where it lives and use the windowed build beside it.
rem Going through the py launcher instead leaves a pyw.exe shim sitting there
rem as a second process for the whole session.
set "PYW_CMD=%PY_CMD%"
set "PY_DIR="
for /f "delims=" %%W in ('%PY_CMD% -c "import sys,os;print(os.path.dirname(sys.executable))" 2^>nul') do set "PY_DIR=%%W"
if defined PY_DIR if exist "!PY_DIR!\pythonw.exe" set PYW_CMD="!PY_DIR!\pythonw.exe"
exit /b 0


rem ══════════════════════════════════════════════════════════════════════════
rem  Install Python: winget first, then a direct download from python.org.
rem  Both install per-user, so neither needs administrator rights.
rem ══════════════════════════════════════════════════════════════════════════
:install_python
where winget >nul 2>&1
if errorlevel 1 (
    echo   winget is not available here, going straight to python.org.
    goto :download_python
)

for %%I in (Python.Python.3.13 Python.Python.3.12 Python.Python.3.11) do (
    echo   Installing %%I with winget...
    winget install --exact --id %%I --scope user --silent ^
        --accept-source-agreements --accept-package-agreements
    call :find_python
    if defined PY_CMD exit /b 0
)

echo   winget could not install it. Falling back to python.org...

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

echo   Downloading !PY_FILE! ...
powershell -NoProfile -Command ^
    "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri '!PY_URL!' -OutFile '!PY_TMP!' } catch { exit 1 }"
if errorlevel 1 (
    echo   The download failed.
    exit /b 1
)

echo   Running the Python installer. This can take a minute...
"!PY_TMP!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del "!PY_TMP!" >nul 2>&1
exit /b 0
