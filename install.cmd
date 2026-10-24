@echo off
REM ════════════════════════════════════════════════════════════════════
REM   Arqon Host VPS Bot - One-Click Installer (Windows)
REM ════════════════════════════════════════════════════════════════════
REM
REM   Double-click this file, or run from a terminal:
REM       install.cmd
REM
REM   It checks git + Python, clones the repo, installs dependencies,
REM   and launches the interactive setup wizard (setup.py).
REM
REM   The web dashboard + node agents are Linux features - on Windows
REM   this installs the Discord bot core only.
REM ════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion
title Arqon Host VPS Bot - Installer
set REPO_URL=https://github.com/nishant1477/vpsbot.git
set INSTALL_DIR=%USERPROFILE%\arqon-host

echo.
echo   ==========================================================
echo     A R Q O N   H O S T   -   V P S   B O T
echo     Windows One-Click Installer
echo   ==========================================================
echo.

REM ── 1/5 check git ──────────────────────────────────────────────────
echo   [1/5] Checking git ...
where git >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [x] git is NOT installed.
    echo       Install it from https://git-scm.com/download/win
    echo       ...or in a terminal:  winget install --id Git.Git -e
    echo       Then run install.cmd again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('git --version') do echo   [+] %%v

REM ── 2/5 check python ───────────────────────────────────────────────
echo   [2/5] Checking Python 3.10+ ...
set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PY=py -3
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set PY=python
)
if not defined PY (
    echo.
    echo   [x] Python 3.10+ is NOT installed.
    echo       Get it from https://www.python.org/downloads/windows/
    echo       IMPORTANT: tick "Add Python to PATH" during install!
    echo       ...or in a terminal:  winget install --id Python.Python.3.12 -e
    echo       Then run install.cmd again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PY% --version') do echo   [+] %%v

REM ── 3/5 get the code ───────────────────────────────────────────────
echo   [3/5] Getting the source code ...
if exist "%~dp0bot.py" (
    set "INSTALL_DIR=%~dp0"
    echo   [+] Running inside the repo - installing in place: !INSTALL_DIR!
) else if exist "%INSTALL_DIR%\bot.py" (
    echo   [+] Existing install found at %INSTALL_DIR% - updating ...
    cd /d "%INSTALL_DIR%"
    git pull --ff-only origin main
) else (
    echo   [*] Cloning %REPO_URL% ...
    git clone --depth 1 "%REPO_URL%" "%INSTALL_DIR%"
    if errorlevel 1 (
        echo   [x] git clone failed - check your internet connection.
        pause
        exit /b 1
    )
    cd /d "%INSTALL_DIR%"
)
echo   [+] Source ready in %INSTALL_DIR%

REM ── 4/5 python deps ────────────────────────────────────────────────
echo   [4/5] Creating virtualenv + installing dependencies ...
if not exist venv (
    %PY% -m venv venv
    if errorlevel 1 (
        echo   [x] Could not create a virtualenv.
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo   [x] Dependency install failed - see messages above.
    pause
    exit /b 1
)
echo   [+] Dependencies installed

REM ── 5/5 setup wizard ───────────────────────────────────────────────
echo   [5/5] Launching setup wizard ...
echo.
python setup.py

echo.
echo   ==========================================================
echo     Setup finished!
echo     Start the bot anytime with:  start-bot.cmd
echo     (from %INSTALL_DIR%)
echo   ==========================================================
echo.
choice /c YN /m "Start the bot right now"
if errorlevel 2 goto :end
python bot.py

:end
pause
