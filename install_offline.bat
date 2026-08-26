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
echo [1/6] Checking Python (3.11 / 3.12 / 3.13)...

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
echo [2/6] Preflight check of the bundle...

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
echo [3/6] Creating virtual environment...

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
echo [4/6] Installing dependencies from offline wheels...

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
echo [5/6] Configuring .env...

if not exist ".env" (
    if exist "offline_bundle\env.stand.example" (
        copy "offline_bundle\env.stand.example" ".env" >nul
        echo    .env created from offline_bundle\env.stand.example
    ) else (
        copy ".env.example" ".env" >nul
        echo    .env created from .env.example
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
echo [6/6] Vector database...

:: The bundle ships a prebuilt DB. Recomputing 2108 vectors on the stand takes
:: 10-15 minutes and is pure waste when the vectors are already here.
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
        echo    No prebuilt database - building it locally (10-15 min)...
        venv\Scripts\python.exe src\build_vectordb.py
        if !errorlevel! neq 0 (
            echo    ERROR: Vector DB build failed
            pause
            exit /b 1
        )
        echo    OK: Vector database built
    )
)

echo.
echo    Final check...
venv\Scripts\python.exe scripts\check_offline.py --stage post
if !errorlevel! neq 0 (
    echo.
    echo    Installation finished, but the check found problems - see above.
    pause
    exit /b 1
)

echo.
echo =====================================================
echo   OFFLINE Installation complete!
echo   Start the service:
echo     venv\Scripts\python.exe -m uvicorn src.api_server:app ^
echo         --host 0.0.0.0 --port 8010
echo   Or run launch.bat for the interactive menu.
echo =====================================================
echo.
pause
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
