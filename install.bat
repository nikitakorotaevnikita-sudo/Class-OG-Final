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
:: STEP 1: Find Python 3.11 / 3.12 / 3.13
:: =============================================================
echo [1/5] Checking Python (3.11 / 3.12 / 3.13)...

set PYTHON=
for %%T in (3.13 3.12 3.11) do (
    if not defined PYTHON (
        py -%%T --version >nul 2>&1
        if !errorlevel! == 0 (
            for /f "tokens=*" %%V in ('py -%%T --version 2^>^&1') do set PYVER=%%V
            set PYTHON=py -%%T
            echo    OK: !PYVER! [via py launcher]
        )
    )
)

:: Fallback: bare python / python3.X executables on PATH
if not defined PYTHON (
    for %%E in (python3.13 python3.12 python3.11 python) do (
        if not defined PYTHON (
            %%E --version >nul 2>&1
            if !errorlevel! == 0 (
                for /f "tokens=*" %%V in ('%%E --version 2^>^&1') do set PYVER=%%V
                echo !PYVER! | findstr /R "3\.11\. 3\.12\. 3\.13\." >nul
                if !errorlevel! == 0 (
                    set PYTHON=%%E
                    echo    OK: !PYVER!
                )
            )
        )
    )
)

if not defined PYTHON (
    echo.
    echo    ERROR: Python 3.11 / 3.12 / 3.13 not found.
    echo.
    echo    Download one of:
    echo      https://www.python.org/downloads/release/python-31315/
    echo      https://www.python.org/downloads/release/python-31210/
    echo      https://www.python.org/downloads/release/python-3119/
    echo    IMPORTANT: Check "Add Python to PATH" during install.
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
:: STEP 4: Configure .env
:: =============================================================
echo.
echo [4/5] Checking .env configuration...

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo    Created .env from .env.example
)

:: --- Select LLM Provider ---
echo.
echo    Select LLM provider:
echo      [1] Groq         - llama-3.3-70b-versatile (recommended, free)
echo      [2] Gemini       - gemini-2.5-flash (20 req/day free)
echo      [3] Ollama/Qwen  - qwen2.5-14b (local, no limits)
echo      [4] Ario         - Qwen3.6-35B-A3B (Directum360)
echo.
set /p PROVIDER_CHOICE=   Enter (1/2/3/4):

if "%PROVIDER_CHOICE%"=="2" (
    set LLM_PROVIDER=gemini
) else if "%PROVIDER_CHOICE%"=="3" (
    set LLM_PROVIDER=ollama
) else if "%PROVIDER_CHOICE%"=="4" (
    set LLM_PROVIDER=ario
) else (
    set LLM_PROVIDER=groq
)

:: Update LLM_PROVIDER in .env
powershell -Command "(Get-Content '.env') -replace 'LLM_PROVIDER=.*', 'LLM_PROVIDER=!LLM_PROVIDER!' | Set-Content '.env' -Encoding UTF8"
echo    LLM Provider: !LLM_PROVIDER!

:: --- Ask for API key based on provider ---
if "!LLM_PROVIDER!"=="groq" (
    echo.
    echo    Get your free key at: https://console.groq.com/keys
    echo    The key looks like: gsk_xxxxxxxxxxxxxxxxxxxx
    echo.
    set /p USER_KEY=   Enter GROQ_API_KEY:
    if defined USER_KEY (
        echo !USER_KEY! | findstr /C:"gsk_" >nul 2>&1
        if !errorlevel! neq 0 (
            echo    WARNING: Key should start with gsk_ -- saving as-is
        )
        powershell -Command "(Get-Content '.env') -replace 'GROQ_API_KEY=.*', 'GROQ_API_KEY=!USER_KEY!' | Set-Content '.env' -Encoding UTF8"
        echo    OK: GROQ_API_KEY saved to .env
    ) else (
        echo    SKIPPED: No key entered. Edit .env manually later.
    )
)

if "!LLM_PROVIDER!"=="gemini" (
    echo.
    echo    Get your free key at: https://ai.google.dev/
    echo    The key looks like: AIzaXXXXXXXXXXXXXXXXXXX
    echo.
    set /p USER_KEY=   Enter GEMINI_API_KEY:
    if defined USER_KEY (
        powershell -Command "(Get-Content '.env') -replace 'GEMINI_API_KEY=.*', 'GEMINI_API_KEY=!USER_KEY!' | Set-Content '.env' -Encoding UTF8"
        echo    OK: GEMINI_API_KEY saved to .env
    ) else (
        echo    SKIPPED: No key entered. Edit .env manually later.
    )
)

if "!LLM_PROVIDER!"=="ario" (
    echo.
    echo    Enter your Ario API token:
    echo.
    set /p USER_KEY=   Enter ARIO_API_KEY:
    if defined USER_KEY (
        :: Check if ARIO_API_KEY line exists in .env
        findstr /C:"ARIO_API_KEY" .env >nul 2>&1
        if !errorlevel!==0 (
            powershell -Command "(Get-Content '.env') -replace 'ARIO_API_KEY=.*', 'ARIO_API_KEY=!USER_KEY!' | Set-Content '.env' -Encoding UTF8"
        ) else (
            echo ARIO_API_KEY=!USER_KEY!>> .env
        )
        echo    OK: ARIO_API_KEY saved to .env
    ) else (
        echo    SKIPPED: No key entered. Edit .env manually later.
    )
    echo.
    set /p ARIO_URL=   Enter ARIO_BASE_URL [https://llm.ario.directum360.ru/v1]:
    if not defined ARIO_URL set ARIO_URL=https://llm.ario.directum360.ru/v1
    findstr /C:"ARIO_BASE_URL" .env >nul 2>&1
    if !errorlevel!==0 (
        powershell -Command "(Get-Content '.env') -replace 'ARIO_BASE_URL=.*', 'ARIO_BASE_URL=!ARIO_URL!' | Set-Content '.env' -Encoding UTF8"
    ) else (
        echo ARIO_BASE_URL=!ARIO_URL!>> .env
    )
    echo    OK: ARIO_BASE_URL = !ARIO_URL!
)

if "!LLM_PROVIDER!"=="ollama" (
    echo.
    echo    Install Ollama: https://ollama.com
    echo    Then run: ollama pull qwen2.5-14b
    echo.
)

:: --- Configure RX Integration ---
echo.
echo    Configure Directum RX integration:
echo    (press Enter to keep defaults)
echo.
set /p RX_URL=   RX OData URL [http://172.16.96.98/integration/odata]:
if not defined RX_URL set RX_URL=http://172.16.96.98/integration/odata
set /p RX_USR=   RX User [Administrator]:
if not defined RX_USR set RX_USR=Administrator
:: Пароль НЕ подставляем: раньше сюда прописывался пароль стенда, и сервис
:: молча ходил в RX под чужой учёткой. Пусто - значит вызов по document_id
:: вернёт 502, и это видно сразу.
set /p RX_PWD=   RX Password (Enter = skip; needed only for calls by document_id):
if not defined RX_PWD echo    WARN: RX password empty - calls by document_id will fail with 502

powershell -Command "(Get-Content '.env') -replace 'RX_ODATA_URL=.*', 'RX_ODATA_URL=!RX_URL!' | Set-Content '.env' -Encoding UTF8"
powershell -Command "(Get-Content '.env') -replace 'RX_USER=.*', 'RX_USER=!RX_USR!' | Set-Content '.env' -Encoding UTF8"
powershell -Command "(Get-Content '.env') -replace 'RX_PASSWORD=.*', 'RX_PASSWORD=!RX_PWD!' | Set-Content '.env' -Encoding UTF8"
echo    OK: RX integration configured

echo    OK: .env configured

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
