@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
cd /d "%PROJECT%"

echo.
echo =====================================================
echo   Class OG Final -- OFFLINE Install
echo   (no internet required)
echo =====================================================
echo.

if not exist "offline_bundle\wheels" (
    echo    ERROR: offline_bundle\wheels not found.
    echo    Build the bundle on a machine WITH internet:
    echo        python scripts\make_offline_bundle.py --python-version 3.11
    echo.
    pause
    exit /b 1
)

:: =============================================================
:: STEP 1: Find Python 3.11 / 3.12 / 3.13
:: =============================================================
echo [1/7] Checking Python (3.11 / 3.12 / 3.13)...

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
:: STEP 2: Preflight -- do the bundled wheels match this Python?
:: =============================================================
echo.
echo [2/7] Preflight check of the bundle...

:: Compiled wheels (torch, numpy, scipy) are tied to the interpreter ABI.
:: A bundle built for 3.13 CANNOT be installed on 3.11 -- fail here with a
:: clear message instead of a wall of pip resolver errors.
%PYTHON% scripts\check_offline.py --stage pre
if !errorlevel! neq 0 (
    echo.
    echo    Preflight found blocking problems -- see the list above.
    echo    .env problems are fixed by this installer in steps 4-5,
    echo    so re-run it after fixing wheels / model / Python version.
    echo.
    choice /C YN /N /M "   Continue anyway? (Y/N): "
    if !errorlevel! neq 1 exit /b 1
)

:: =============================================================
:: STEP 3: Create venv
:: =============================================================
echo.
echo [3/7] Creating virtual environment...

:: A venv copied from another machine is broken: the launchers in Scripts\
:: hardcode the absolute path of the interpreter that created them.
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -m pip --version >nul 2>&1
    if !errorlevel! == 0 (
        echo    OK: venv already exists and works
    ) else (
        echo    WARN: existing venv is broken - recreating
        rmdir /s /q venv
    )
)

if not exist "venv\Scripts\python.exe" (
    %PYTHON% -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo    ERROR: Failed to create venv
        pause
        exit /b 1
    )
    echo    OK: venv created
)

:: =============================================================
:: STEP 4: Install from local wheels (NO INTERNET)
:: =============================================================
echo.
echo [4/7] Installing dependencies from offline wheels...

:: python -m pip, never pip.exe: the launcher may be stale after a copy.
venv\Scripts\python.exe -m pip install --no-index --find-links=offline_bundle\wheels --upgrade pip --quiet 2>nul
venv\Scripts\python.exe -m pip install --no-index --find-links=offline_bundle\wheels -r requirements.txt
if !errorlevel! neq 0 (
    echo.
    echo    ERROR: pip install from wheels failed.
    echo    Most likely the wheels were built for a different Python minor.
    echo    Rebuild on a machine with internet:
    echo        python scripts\make_offline_bundle.py --python-version ^<this Python^>
    echo.
    pause
    exit /b 1
)
echo    OK: Dependencies installed (offline)

:: =============================================================
:: STEP 5: Configure .env
:: =============================================================
echo.
echo [5/7] Configuring .env...

if not exist ".env" (
    if exist "offline_bundle\env.stand.example" (
        copy "offline_bundle\env.stand.example" ".env" >nul
        echo    .env created from offline_bundle\env.stand.example
    ) else (
        copy ".env.example" ".env" >nul
        echo    .env created from .env.example
    )
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

echo.
echo    LLM provider. On an isolated stand only in-network options work:
echo      [1] custom  - any OpenAI-compatible endpoint (vLLM, LM Studio, gpt-oss)
echo      [2] ollama  - model running on this machine
echo      [3] ario    - Directum360 (NEEDS INTERNET)
echo.
set /p PROVIDER_CHOICE=   Enter (1/2/3) [1]:

if "%PROVIDER_CHOICE%"=="2" (
    set LLM_PROVIDER=ollama
) else if "%PROVIDER_CHOICE%"=="3" (
    set LLM_PROVIDER=ario
) else (
    set LLM_PROVIDER=custom
)

call :setenv LLM_PROVIDER "!LLM_PROVIDER!"
echo    LLM Provider: !LLM_PROVIDER!

if "!LLM_PROVIDER!"=="custom" (
    echo.
    set /p C_URL=   Endpoint base URL (e.g. http://10.0.0.5:8000/v1):
    if defined C_URL call :setenv CUSTOM_LLM_BASE_URL "!C_URL!"
    set /p C_MODEL=   Model name as the endpoint reports it:
    if defined C_MODEL call :setenv CUSTOM_LLM_MODEL "!C_MODEL!"
    set /p C_KEY=   API key (Enter = none):
    if defined C_KEY call :setenv CUSTOM_LLM_API_KEY "!C_KEY!"
)

if "!LLM_PROVIDER!"=="ollama" (
    echo.
    set /p O_URL=   Ollama base URL [http://localhost:11434]:
    if not defined O_URL set O_URL=http://localhost:11434
    call :setenv OLLAMA_BASE_URL "!O_URL!"
)

if "!LLM_PROVIDER!"=="ario" (
    echo.
    set /p USER_KEY=   Enter ARIO_API_KEY:
    if defined USER_KEY call :setenv ARIO_API_KEY "!USER_KEY!"
    set /p ARIO_URL=   Enter ARIO_BASE_URL [https://llm.ario.directum360.ru/v1]:
    if not defined ARIO_URL set ARIO_URL=https://llm.ario.directum360.ru/v1
    call :setenv ARIO_BASE_URL "!ARIO_URL!"
)

:: --- RX integration: only needed for calls by document_id -------------------
echo.
echo    Directum RX integration (Enter = skip).
echo    Needed ONLY for calls by document_id; if RX sends the appeal
echo    text in the request, leave all three empty.
echo.
set /p RX_URL=   RX OData URL:
if defined RX_URL call :setenv RX_ODATA_URL "!RX_URL!"
set /p RX_USR=   RX User:
if defined RX_USR call :setenv RX_USER "!RX_USR!"
set /p RX_PWD=   RX Password:
if defined RX_PWD (
    call :setenv RX_PASSWORD "!RX_PWD!"
) else (
    echo    NOTE: RX password empty - calls by document_id will fail with 502
)

:: --- Offline flags and local model -----------------------------------------
:: HF_HUB_OFFLINE is read once, when huggingface_hub is imported. Without it
:: sentence-transformers tries to reach the network and dies with
:: FileMetadataError even though the model files are right here.
call :setenv HF_HUB_OFFLINE "1"
call :setenv TRANSFORMERS_OFFLINE "1"
call :setenv EMBEDDING_MODEL "offline_bundle/models/multilingual-e5-base"
call :setenv ENABLE_EMBEDDING_ADAPTER "false"

if exist "offline_bundle\models\multilingual-e5-base\config.json" (
    echo    OK: Embedding model found locally
) else (
    echo    ERROR: model missing in offline_bundle\models\
    echo    Rebuild the bundle: python scripts\make_offline_bundle.py
    pause
    exit /b 1
)
echo    OK: .env configured

:: =============================================================
:: STEP 6: Vector DB + final check
:: =============================================================
echo.
echo [6/7] Vector database...

:: The bundle ships the vector files. They are the source that "start.cmd migrate"
:: imports into PostgreSQL; nothing is built on the stand.
set "VDB_NAME=vector_db"
for /f "tokens=2 delims==" %%a in ('findstr /R "^VECTOR_DB_DIR=" .env 2^>nul') do set "VDB_CFG=%%a"
if defined VDB_CFG (
    for %%p in ("!VDB_CFG!") do set "VDB_NAME=%%~nxp"
)
set "VDB_DIR=data\!VDB_NAME!"

if exist "!VDB_DIR!\embeddings.npy" (
    echo    OK: Vector database already present: !VDB_DIR!
) else (
    if exist "offline_bundle\vector_db_prebuilt\embeddings.npy" (
        if not exist "!VDB_DIR!" mkdir "!VDB_DIR!"
        copy "offline_bundle\vector_db_prebuilt\embeddings.npy" "!VDB_DIR!\" >nul
        copy "offline_bundle\vector_db_prebuilt\metadata.json" "!VDB_DIR!\" >nul
        echo    OK: prebuilt database copied to !VDB_DIR!
    ) else (
        echo    WARNING: no vector files in !VDB_DIR! - "start.cmd migrate" will fail
    )
)
echo    Vectors are imported into PostgreSQL by "start.cmd migrate"
echo    (start.cmd all does it automatically; Docker: compose service migrate).

echo.
echo    Final check...
venv\Scripts\python.exe scripts\check_offline.py --stage post
if !errorlevel! neq 0 (
    echo.
    echo    Installation finished, but the check found problems - see above.
    pause
    exit /b 1
)

:: =============================================================
:: STEP 7: PostgreSQL in Docker, migration
:: =============================================================
:: As in install.bat, but the image cannot be pulled without internet: when it
:: is not loaded, setup_infra.cmd explains docker save / docker load and skips.
:: A worker-only machine skips this step: its database is on the main server.
echo.
set "INFRA_FAILED=!DB_FAILED!"
if "!WORKER_ONLY!"=="1" echo [7/7] Worker-only machine: PostgreSQL is not started here ^(DATABASE_URL^)
if "!WORKER_ONLY!"=="1" goto :infra_done
echo [7/7] PostgreSQL in Docker, data migration
call "%PROJECT%\scripts\setup_infra.cmd" offline
if errorlevel 1 set "INFRA_FAILED=1"
:infra_done

echo.
echo =====================================================
if "!INFRA_FAILED!"=="1" (
    echo   OFFLINE installation finished, but the infrastructure step failed - see above.
) else (
    echo   OFFLINE Installation complete!
)
if "!WORKER_ONLY!"=="1" (
    echo   Worker machine: start.cmd worker ^(launch.bat item 3^), stop: stop.cmd worker
    echo   MODELS_DIR in .env: a folder shared with the main server, or its copy.
) else (
    echo   Start the service ^(PostgreSQL in Docker, API and workers^):
    echo     launch.bat server  =  start.cmd all      ^(stop: stop.cmd^)
    echo   Or run launch.bat for the interactive menu.
)
echo =====================================================
echo.
pause
exit /b !INFRA_FAILED!

:: =============================================================
:: ask_db_url -- DATABASE_URL of the main server into .env (worker machine)
:: =============================================================
:: The value goes to PowerShell through an environment variable, not inside
:: the command text: a password may hold quotes or $ signs. Only host:port is
:: printed. Then a 5 s connection check (warning only).
:ask_db_url
echo.
echo    Database of the main server. There: POSTGRES_BIND=0.0.0.0, an open port
echo    and a password other than classifier. Example:
echo      postgresql://user:password@host:5432/classifier
set "DB_URL="
set /p DB_URL=   DATABASE_URL:
if not defined DB_URL (
    echo    ERROR: DATABASE_URL is empty - a worker machine needs the main server database.
    exit /b 1
)
set "DB_URL_IN=!DB_URL!"
powershell -NoProfile -Command "$v = $env:DB_URL_IN; if ($v -notmatch '^postgres(ql)?://') { Write-Output '   ERROR: DATABASE_URL must start with postgresql://'; exit 1 }; $p = '.env'; $found = $false; $out = @(Get-Content $p -Encoding UTF8 | ForEach-Object { if ($_ -match '^DATABASE_URL=') { $found = $true; 'DATABASE_URL=' + $v } else { $_ } }); if (-not $found) { $out += ('DATABASE_URL=' + $v) }; Set-Content $p -Value $out -Encoding UTF8; try { $u = [uri]$v; $port = 5432; if ($u.Port -gt 0) { $port = $u.Port }; Write-Output ('   OK: DATABASE_URL saved to .env (database ' + $u.Host + ':' + $port + ')') } catch { Write-Output '   OK: DATABASE_URL saved to .env' }"
if errorlevel 1 exit /b 1
set "DATABASE_URL=!DB_URL_IN!"
set "DB_URL_IN="
venv\Scripts\python.exe -c "import os, sys; sys.excepthook = lambda t, e, tb: print('   ', t.__name__, str(e).strip().splitlines()[0] if str(e).strip() else '', file=sys.stderr); import psycopg; psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5).close()"
if errorlevel 1 (
    echo    WARNING: the database did not answer within 5 s - check DATABASE_URL in .env,
    echo             POSTGRES_BIND=0.0.0.0 and the open port on the main server.
    set "DB_FAILED=1"
) else (
    echo    OK: the main server database answers
)
exit /b 0

:: =============================================================
:: setenv KEY VALUE -- replace the line in .env or append it
:: =============================================================
:setenv
set "K=%~1"
set "V=%~2"
findstr /R "^%K%=" .env >nul 2>&1
if !errorlevel!==0 (
    powershell -NoProfile -Command "$p='.env'; $k='%K%'; $v='%V%'; (Get-Content $p -Encoding UTF8) | ForEach-Object { if ($_ -match ('^' + [regex]::Escape($k) + '=')) { $k + '=' + $v } else { $_ } } | Set-Content $p -Encoding UTF8"
) else (
    powershell -NoProfile -Command "$p='.env'; Add-Content -Path $p -Value ('%K%=' + '%V%') -Encoding UTF8"
)
exit /b 0
