@echo off
setlocal EnableDelayedExpansion
rem pushd rather than cd: a UNC path gets a temporary drive letter, where cd
rem would refuse and leave us running from the wrong folder.
pushd "%~dp0"
title Star Citizen Helper

rem /update is the app's "Update now" button: fetch even if auto_update is off.
set "UPDATE_ARGS="
if /i "%~1"=="/update" set "UPDATE_ARGS=--force"

rem Everything below is a check first and an action only if needed, so a normal
rem launch costs a fraction of a second. First run does the whole setup.

rem ── App files ─────────────────────────────────────────────────────────────
rem The updater is helper/update.py - inside the very tree it fetches. So a
rem launcher on its own, which the README says is all you need, had nothing
rem to run it with: "No module named helper.update", ignored, then a launch
rem of a file that was not there. This fetches the tree first, once, and
rem only when it is missing. Python is not needed for it; PowerShell is.
if exist "%~dp0helper\update.py" goto :have_files

rem Double-clicking the .bat inside the zip in Explorer extracts just that one
rem file to a temp folder and runs it there. Installing into that folder would
rem vanish with it, so say what to do instead.
echo "%~dp0"| findstr /i /l /c:"Temp1_" /c:"AppData\Local\Temp" >nul
if not errorlevel 1 (
    echo.
    echo   This launcher is running from a temporary folder - usually because it
    echo   was opened from inside the zip. Save StarCitizenHelper.bat into a folder
    echo   of its own, then double-click it there.
    echo.
    pause
    exit /b 1
)

echo.
echo   Setting up Star Citizen Helper for the first time.
echo   Fetching the app files - about 320 KB...
echo.
set "SCH_BOOT_ZIP=%TEMP%\StarCitizenHelper-boot.zip"
set "SCH_BOOT_DIR=%TEMP%\StarCitizenHelper-boot"
rem Paths reach PowerShell through the environment, not inside its quotes: a
rem name with an apostrophe in it would end a single-quoted string early.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue';" ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072;" ^
    "try {" ^
    "  Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri 'https://codeload.github.com/cjbrochacho/StarCitizenHelper/zip/refs/heads/main' -OutFile $env:SCH_BOOT_ZIP;" ^
    "  if (Test-Path -LiteralPath $env:SCH_BOOT_DIR) { Remove-Item -LiteralPath $env:SCH_BOOT_DIR -Recurse -Force };" ^
    "  Expand-Archive -LiteralPath $env:SCH_BOOT_ZIP -DestinationPath $env:SCH_BOOT_DIR -Force" ^
    "} catch { Write-Host ('  ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo.
    echo   [ERROR] The download failed. Check your connection and run this again.
    echo.
    pause
    exit /b 1
)

rem The folder name inside the archive is fixed by the branch - see
rem helper.update.download, which fetches this same zip.
if not exist "%SCH_BOOT_DIR%\StarCitizenHelper-main\helper\update.py" (
    echo.
    echo   [ERROR] What was downloaded did not look like the app, so it was not installed.
    echo.
    rmdir /s /q "%SCH_BOOT_DIR%" >nul 2>&1
    del /q "%SCH_BOOT_ZIP%" >nul 2>&1
    pause
    exit /b 1
)

if not exist "%~dp0assets" mkdir "%~dp0assets" >nul 2>&1
rem Never this file: cmd reads a batch by offset as it runs - see the note at
rem the end. The newer launcher waits in assets\ and is swapped in on exit.
copy /y "%SCH_BOOT_DIR%\StarCitizenHelper-main\StarCitizenHelper.bat" "%~dp0assets\pending.bat" >nul
rem robocopy merges into whatever is already here, so a tree that is only
rem partly there is repaired rather than refused. The trailing dot matters: a
rem quoted path ending in a backslash reads to robocopy as an escaped quote.
robocopy "%SCH_BOOT_DIR%\StarCitizenHelper-main" "%~dp0." /E /XF StarCitizenHelper.bat /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo.
    echo   [ERROR] The app files could not be written here.
    echo.
    pause
    exit /b 1
)
rmdir /s /q "%SCH_BOOT_DIR%" >nul 2>&1
del /q "%SCH_BOOT_ZIP%" >nul 2>&1

if not exist "%~dp0helper\update.py" (
    echo.
    echo   [ERROR] The app files are still missing after the download.
    echo.
    pause
    exit /b 1
)
rem assets\.version is deliberately not written here. helper.update runs a
rem moment later, finds no record of what is installed, and fetches the zip
rem once more - 320 KB, on the first launch only - so that there is exactly
rem one place that decides what counts as installed.

:have_files
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

rem ── Update ────────────────────────────────────────────────────────────────
rem Before the app starts, not after: with nothing loaded yet the files can be
rem replaced cleanly, and what launches a moment later is already the new one.
rem Never fatal - no network just means no update, and the app still runs.
rem What it does report, by exit code, is an install that cannot be trusted:
rem files missing, or an update that only half applied. That message should
rem be read, not closed over.
if not exist "%~dp0assets" mkdir "%~dp0assets" >nul 2>&1
%PY_CMD% -m helper.update %UPDATE_ARGS%
set "UPDATE_RC=%errorlevel%"
if not "%UPDATE_RC%"=="0" (
    if not exist "%~dp0StarCitizenHelper.py" (
        echo.
        echo   [ERROR] The app files are incomplete, so it cannot start. Run this again.
        echo.
        pause
        exit /b 1
    )
    timeout /t 4 >nul
)
if defined UPDATE_ARGS if "%UPDATE_RC%"=="0" timeout /t 3 >nul

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
rem SCH_NO_LAUNCH is for test_launcher.py, which runs this whole file end to
rem end in a scratch folder and does not want a window at the end of it.
if not defined SCH_NO_LAUNCH start "" %PYW_CMD% "%~dp0StarCitizenHelper.py"

rem An update cannot rewrite this file while it is running: cmd reads a batch
rem by file offset as it goes, so replacing it underneath makes it run whatever
rem now sits at that offset. The updater leaves a new one in assets/ and it is
rem swapped in here, on a single line, as the very last thing that happens -
rem the line is already in memory, and nothing is read after it.
if exist "%~dp0assets\pending.bat" (copy /y "%~dp0assets\pending.bat" "%~f0" >nul & del "%~dp0assets\pending.bat" >nul) & exit /b 0

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
