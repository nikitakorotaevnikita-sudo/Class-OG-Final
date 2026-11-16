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
echo [1/6] Checking Python (3.11 / 3.12 / 3.13)...

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
echo [2/6] Setting up virtual environment...

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
echo [3/6] Installing dependencies...

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
echo [4/6] Checking .env configuration...

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo    Created .env from .env.example
)

:: --- Worker-only machine ---
:: Workers for the API of another machine: no Docker and no ports here. The
:: database is the main server's DATABASE_URL, written into .env as typed and
:: never printed back (it holds the password).
set "WORKER_ONLY=0"
set "DB_FAILED=0"
set "WO_ANSWER="
echo.
set /p WO_ANSWER=   Is this machine only for workers? [y/N]:
if /i "!WO_ANSWER!"=="y" set "WORKER_ONLY=1"
if /i "!WO_ANSWER!"=="yes" set "WORKER_ONLY=1"
if "!WORKER_ONLY!"=="1" (
    call :ask_db_url
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

:: --- Select LLM Provider ---
echo.
echo    Select LLM provider:
echo      [1] Ario         - Directum360, the project default
echo      [2] Custom       - the customer's OpenAI-compatible endpoint (vLLM)
echo      [3] Ollama/Qwen  - a local model, no limits
echo      [4] Groq         - cloud, free key
echo      [5] Gemini       - cloud, 20 req/day free
echo.
set /p PROVIDER_CHOICE=   Enter (1/2/3/4/5):

if "%PROVIDER_CHOICE%"=="2" (
    set LLM_PROVIDER=custom
) else if "%PROVIDER_CHOICE%"=="3" (
    set LLM_PROVIDER=ollama
) else if "%PROVIDER_CHOICE%"=="4" (
    set LLM_PROVIDER=groq
) else if "%PROVIDER_CHOICE%"=="5" (
    set LLM_PROVIDER=gemini
) else (
    set LLM_PROVIDER=ario
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
    echo    Model name: see GET {base}/models -- the set on the endpoint changes.
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

if "!LLM_PROVIDER!"=="custom" (
    echo.
    echo    OpenAI-compatible endpoint of the customer's model.
    echo    The model name must match GET {base}/models exactly --
    echo    vLLM usually reports a prefixed id, e.g. openai/gpt-oss-20b.
    echo.
    set /p C_URL=   Enter CUSTOM_LLM_BASE_URL (e.g. http://10.0.0.5:8000/v1):
    if defined C_URL (
        powershell -NoProfile -Command "(Get-Content '.env') -replace '^CUSTOM_LLM_BASE_URL=.*', 'CUSTOM_LLM_BASE_URL=!C_URL!' | Set-Content '.env' -Encoding UTF8"
        echo    OK: CUSTOM_LLM_BASE_URL = !C_URL!
    )
    set /p C_MODEL=   Enter CUSTOM_LLM_MODEL:
    if defined C_MODEL (
        powershell -NoProfile -Command "(Get-Content '.env') -replace '^CUSTOM_LLM_MODEL=.*', 'CUSTOM_LLM_MODEL=!C_MODEL!' | Set-Content '.env' -Encoding UTF8"
        echo    OK: CUSTOM_LLM_MODEL = !C_MODEL!
    )
    echo    Leave the key empty if the server does not check it.
    set /p C_KEY=   Enter CUSTOM_LLM_API_KEY:
    if defined C_KEY (
        powershell -NoProfile -Command "(Get-Content '.env') -replace '^CUSTOM_LLM_API_KEY=.*', 'CUSTOM_LLM_API_KEY=!C_KEY!' | Set-Content '.env' -Encoding UTF8"
        echo    OK: CUSTOM_LLM_API_KEY saved to .env
    )
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
:: STEP 5: Vector database
:: =============================================================
:: Vectors live in PostgreSQL now. They are imported from the VECTOR_DB_DIR
:: files (shipped in Git) by "start.cmd migrate" - nothing to build here.
echo.
echo [5/6] Vector database
echo    Vectors are imported into PostgreSQL on step 6 (retry: start.cmd migrate).

:: =============================================================
:: STEP 6: PostgreSQL in Docker, migration
:: =============================================================
:: A free host port goes to .env (a value already there is kept), then
:: docker compose up postgres and start.cmd migrate. No Docker or the
:: engine is stopped: not a failure - run "start.cmd migrate" later.
:: A worker-only machine skips this step: its database is on the main server.
echo.
set "INFRA_FAILED=!DB_FAILED!"
if "!WORKER_ONLY!"=="1" echo [6/6] Worker-only machine: PostgreSQL is not started here ^(DATABASE_URL^)
if "!WORKER_ONLY!"=="1" goto :infra_done
echo [6/6] PostgreSQL in Docker, data migration
call "%PROJECT%\scripts\setup_infra.cmd"
if errorlevel 1 set "INFRA_FAILED=1"
:infra_done

:: =============================================================
:: DONE
:: =============================================================
echo.
echo =====================================================
if "!INFRA_FAILED!"=="1" (
    echo   Installation finished, but the infrastructure step failed - see above.
) else (
    echo   Installation complete! Run launch.bat to start.
)
if "!WORKER_ONLY!"=="1" (
    echo   Worker machine: start.cmd worker ^(launch.bat item 3^), stop: stop.cmd worker
    echo   MODELS_DIR in .env: a folder shared with the main server, or its copy.
) else (
    echo   launch.bat server = start.cmd all ^(API + workers from venv, DB in Docker^)
    echo   Full stack in Docker: start.cmd docker
)
echo =====================================================
echo.
pause
:: Same as install_offline.bat: nonzero when the infrastructure step failed.
exit /b !INFRA_FAILED!

:: =============================================================
:: ask_db_url -- DATABASE_URL of the main server into .env (worker machine)
:: =============================================================
:: All in Python (scripts\check_db.py): cmd never holds the address, so "!"
:: and "%" in the password survive, and the password is typed hidden
:: (getpass). Only host:port is printed. Then a 5 s connection check
:: (warning only).
:ask_db_url
echo.
venv\Scripts\python.exe scripts\check_db.py ask
if errorlevel 1 exit /b 1
venv\Scripts\python.exe scripts\check_db.py check
if errorlevel 1 (
    echo    WARNING: the main server database does not answer - see the reason above.
    set "DB_FAILED=1"
)
exit /b 0
