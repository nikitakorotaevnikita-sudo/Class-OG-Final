@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
cd /d "%PROJECT%"

echo.
echo =====================================================
echo   Citizens Appeals Classification Agent -- Install
echo =====================================================
echo.

:: =============================================================
:: STEP 1: Find Python 3.11
:: =============================================================
echo [1/5] Checking Python 3.11...

set PYTHON=
for %%C in ("py -3.11" "python3.11" "python") do (
    if not defined PYTHON (
        %%~C --version >nul 2>&1
        if !errorlevel! == 0 (
            for /f "tokens=*" %%V in ('%%~C --version 2^>^&1') do set PYVER=%%V
            echo !PYVER! | findstr /C:"3.11" >nul
            if !errorlevel! == 0 (
                set PYTHON=%%~C
                echo    OK: !PYVER!
            )
        )
    )
)

if not defined PYTHON (
    echo.
    echo    ERROR: Python 3.11 not found.
    echo.
    echo    Download: https://www.python.org/downloads/release/python-3119/
    echo    IMPORTANT: Check "Add Python to PATH" during install.
    echo    Use version 3.11.x only -- NOT 3.12 or 3.14
    echo.
    pause
    exit /b 1
)

:: =============================================================
:: STEP 2: Create virtual environment
:: =============================================================
echo.
echo [2/5] Setting up virtual environment...

if exist "venv\Scripts\python.exe" (
    echo    OK: venv already exists, skipping
) else (
    %PYTHON% -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo    ERROR: Failed to create venv
        pause
        exit /b 1
    )
    echo    OK: venv created
)

:: =============================================================
:: STEP 3: Install dependencies
:: =============================================================
echo.
echo [3/5] Installing dependencies...

venv\Scripts\pip.exe install --upgrade pip --quiet
venv\Scripts\pip.exe install -r requirements.txt

if %errorlevel% neq 0 (
    echo    ERROR: pip install failed. Check requirements.txt
    pause
    exit /b 1
)
echo    OK: Dependencies installed

:: =============================================================
:: STEP 4: Check .env
:: =============================================================
echo.
echo [4/5] Checking .env configuration...

if exist ".env" (
    findstr /C:"gsk_" ".env" >nul 2>&1
    if !errorlevel! == 0 (
        echo    OK: .env found with GROQ_API_KEY
    ) else (
        echo    WARN: .env found but GROQ_API_KEY may be missing
        echo    Edit .env and set GROQ_API_KEY=gsk_...
    )
) else (
    echo    .env not found -- copying from .env.example
    copy ".env.example" ".env" >nul
    echo.
    echo    !! ACTION REQUIRED !!
    echo    Open .env and replace PASTE_YOUR_GROQ_API_KEY_HERE
    echo    with your actual key from: https://console.groq.com/keys
    echo.
    echo    Then run install.bat again to build the vector DB.
    pause
    exit /b 0
)

:: =============================================================
:: STEP 5: Build vector database
:: =============================================================
echo.
echo [5/5] Building vector database (5-15 min first time)...

if exist "data\vector_db\embeddings.npy" (
    echo    OK: Vector database already exists, skipping
) else (
    echo    Loading multilingual-e5-base model and vectorizing 2108 entries...
    echo    Please wait...
    echo.
    venv\Scripts\python.exe src/build_vectordb.py
    if %errorlevel% neq 0 (
        echo.
        echo    ERROR: Vector DB build failed. Check output above.
        pause
        exit /b 1
    )
    echo    OK: Vector database built
)

:: =============================================================
:: DONE
:: =============================================================
echo.
echo =====================================================
echo   Installation complete! Run launch.bat to start.
echo =====================================================
echo.
pause
