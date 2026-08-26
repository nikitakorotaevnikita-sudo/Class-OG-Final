@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set "BUNDLE=%~dp0"
if "%BUNDLE:~-1%"=="\" set "BUNDLE=%BUNDLE:~0,-1%"
cd /d "%BUNDLE%"

echo.
echo =====================================================
echo   Class OG Final -- OFFLINE Install
echo   (no internet required)
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
            echo    OK: !PYVER!
        )
    )
)

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
    echo    Install Python FIRST, then re-run this script.
    echo.
    pause
    exit /b 1
)

:: =============================================================
:: STEP 2: Create venv
:: =============================================================
echo.
echo [2/5] Creating virtual environment...

if exist "venv\Scripts\python.exe" (
    echo    OK: venv already exists
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
:: STEP 3: Install from local wheels (NO INTERNET)
:: =============================================================
echo.
echo [3/5] Installing dependencies from offline wheels...

venv\Scripts\pip.exe install --upgrade pip --no-index --find-links=offline_bundle\wheels --quiet 2>nul
venv\Scripts\pip.exe install --no-index --find-links=offline_bundle\wheels -r requirements.txt

if %errorlevel% neq 0 (
    echo    ERROR: pip install from wheels failed
    echo    Make sure all wheels are present in offline_bundle\wheels\
    pause
    exit /b 1
)
echo    OK: Dependencies installed (offline)

:: =============================================================
:: STEP 4: Configure .env
:: =============================================================
echo.
echo [4/5] Configuring .env...

if not exist ".env" (
    copy ".env.example" ".env" >nul
)

echo.
echo    Select LLM provider:
echo      [1] Groq         - llama-3.3-70b-versatile
echo      [2] Gemini       - gemini-2.5-flash
echo      [3] Ollama/Qwen  - qwen2.5-14b (local)
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

powershell -Command "(Get-Content '.env') -replace 'LLM_PROVIDER=.*', 'LLM_PROVIDER=!LLM_PROVIDER!' | Set-Content '.env' -Encoding UTF8"
echo    LLM Provider: !LLM_PROVIDER!

if "!LLM_PROVIDER!"=="ario" (
    echo.
    set /p USER_KEY=   Enter ARIO_API_KEY:
    if defined USER_KEY (
        findstr /C:"ARIO_API_KEY" .env >nul 2>&1
        if !errorlevel!==0 (
            powershell -Command "(Get-Content '.env') -replace 'ARIO_API_KEY=.*', 'ARIO_API_KEY=!USER_KEY!' | Set-Content '.env' -Encoding UTF8"
        ) else (
            echo ARIO_API_KEY=!USER_KEY!>> .env
        )
    )
    set /p ARIO_URL=   Enter ARIO_BASE_URL [https://llm.ario.directum360.ru/v1]:
    if not defined ARIO_URL set ARIO_URL=https://llm.ario.directum360.ru/v1
    findstr /C:"ARIO_BASE_URL" .env >nul 2>&1
    if !errorlevel!==0 (
        powershell -Command "(Get-Content '.env') -replace 'ARIO_BASE_URL=.*', 'ARIO_BASE_URL=!ARIO_URL!' | Set-Content '.env' -Encoding UTF8"
    ) else (
        echo ARIO_BASE_URL=!ARIO_URL!>> .env
    )
)

if "!LLM_PROVIDER!"=="groq" (
    echo.
    set /p USER_KEY=   Enter GROQ_API_KEY (gsk_...):
    if defined USER_KEY (
        powershell -Command "(Get-Content '.env') -replace 'GROQ_API_KEY=.*', 'GROQ_API_KEY=!USER_KEY!' | Set-Content '.env' -Encoding UTF8"
    )
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
:: STEP 5: Setup embedding model + vector DB (offline)
:: =============================================================
echo.
echo [5/5] Setting up embedding model and vector DB...

:: Point to local model
powershell -Command "$env = Get-Content '.env'; if ($env -notmatch 'EMBEDDING_MODEL=') { Add-Content '.env' 'EMBEDDING_MODEL=offline_bundle/models/multilingual-e5-base' } else { (Get-Content '.env') -replace 'EMBEDDING_MODEL=.*','EMBEDDING_MODEL=offline_bundle/models/multilingual-e5-base' | Set-Content '.env' -Encoding UTF8 }"

if exist "offline_bundle\models\multilingual-e5-base\config.json" (
    echo    OK: Embedding model found locally
) else (
    echo    WARNING: Model not found in offline_bundle\models\
    echo    You may need to copy it manually or build vector DB with internet.
)

if exist "data\vector_db\embeddings.npy" (
    echo    OK: Vector database already exists
) else (
    echo    Building vector database from local model...
    venv\Scripts\python.exe src/build_vectordb.py
    if %errorlevel% neq 0 (
        echo    ERROR: Vector DB build failed
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
echo   OFFLINE Installation complete!
echo   Run launch.bat to start the application.
echo =====================================================
echo.
pause
