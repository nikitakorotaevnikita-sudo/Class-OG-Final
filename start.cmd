@echo off
setlocal enabledelayedexpansion

:: Start the appeals classifier (Windows). Linux analogue: start.sh
::
::   start.cmd [docker]            whole stack in Docker (default)
::   start.cmd local [--port N]    PostgreSQL + RabbitMQ in Docker, API and workers from venv
::   start.cmd migrate             schema + file import only (the DB must be running)
::
::   --training                    also start the fine-tuning worker
::
:: Stop: stop.cmd [docker or local]. This file stays ASCII-only: cmd.exe reads
:: it in the console code page (CP866/CP1251) and breaks on UTF-8 text.

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
cd /d "%PROJECT%"

set "PY=venv\Scripts\python.exe"
set "MODE=docker"
set "TRAINING=0"
set "PORT_ARG="

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="docker"     ( set "MODE=docker"  & goto :next )
if /i "%~1"=="local"      ( set "MODE=local"   & goto :next )
if /i "%~1"=="migrate"    ( set "MODE=migrate" & goto :next )
if /i "%~1"=="--training" ( set "TRAINING=1"   & goto :next )
if /i "%~1"=="--port" goto :port_arg
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo Unknown argument: %~1  ^(see --help^)
exit /b 2
:port_arg
if "%~2"=="" (
    echo ERROR: --port needs a port number
    exit /b 2
)
set "PORT_ARG=%~2"
shift
:next
shift
goto :parse
:parsed

:: Compose reads .env (env_file and the bind mount). Without the file Docker
:: creates a directory in its place and the back office cannot save settings.
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo WARNING: .env not found - created from .env.example.
    echo          Fill in the LLM keys and passwords, then restart.
)

if /i "%MODE%"=="docker"  goto :docker
if /i "%MODE%"=="local"   goto :local
if /i "%MODE%"=="migrate" goto :migrate_only
exit /b 2

:: ---------------------------------------------------------------- docker
:docker
call :require_docker || exit /b 1
set "PROFILE="
if "%TRAINING%"=="1" set "PROFILE=--profile training"
echo.
echo === Starting the stack in Docker ===
docker compose %PROFILE% up -d --build
if errorlevel 1 (
    echo ERROR: docker compose up failed. See: docker compose logs migrate api
    exit /b 1
)
call :read_env HOST_PORT 8010 PORT
call :wait_health !PORT! || exit /b 1
echo   Back office: http://localhost:!PORT!/backoffice
exit /b 0

:: ---------------------------------------------------------------- local
:local
call :require_docker || exit /b 1
call :require_venv || exit /b 1
echo.
echo === Local mode: infrastructure in Docker, service from venv ===
echo [0] PostgreSQL and RabbitMQ
docker compose up -d --wait postgres rabbitmq
if errorlevel 1 (
    echo ERROR: postgres/rabbitmq did not become healthy. See: docker compose logs postgres rabbitmq
    exit /b 1
)
call :run_migrate || exit /b 1

call :read_env API_PORT 8010 PORT
if defined PORT_ARG set "PORT=%PORT_ARG%"
call :read_env API_HOST 0.0.0.0 HOST
call :read_env CLASSIFY_WORKERS 2 WORKERS
set /a WORKERS=WORKERS+0
if !WORKERS! LSS 1 set "WORKERS=1"

:: Child windows inherit this environment (PYTHONPATH) and the working dir.
:: Window titles start with "classifier-": stop.cmd local closes them by title.
:: cmd /k keeps a window open after a crash so the error stays readable.
echo [3] API and !WORKERS! classify worker^(s^)
start "classifier-api" /D "%PROJECT%" cmd /k %PY% -m uvicorn src.api_server:app --host !HOST! --port !PORT!
:: The solo pool runs one task per process (prefork does not work on Windows),
:: so parallelism comes from CLASSIFY_WORKERS separate processes.
for /L %%i in (1,1,!WORKERS!) do (
    start "classifier-worker-%%i" /D "%PROJECT%" cmd /k %PY% -m celery -A queueing.celery_app worker -Q classify_sync,classify --pool=solo -n classify%%i@%COMPUTERNAME% --loglevel=INFO
)
if "%TRAINING%"=="1" (
    start "classifier-worker-training" /D "%PROJECT%" cmd /k %PY% -m celery -A queueing.celery_app worker -Q training --pool=solo -n training@%COMPUTERNAME% --loglevel=INFO
)
call :wait_health !PORT! || exit /b 1
echo   Back office: http://localhost:!PORT!/backoffice    Stop: stop.cmd local
exit /b 0

:: ---------------------------------------------------------------- migrate
:migrate_only
call :require_venv || exit /b 1
call :run_migrate || exit /b 1
echo   OK: migration finished
exit /b 0

:: ================================================================ helpers

:run_migrate
set "PYTHONPATH=%PROJECT%\src"
echo [1] Database schema
%PY% scripts\db\init_db.py
if errorlevel 1 (
    echo ERROR: schema was not applied - is PostgreSQL reachable at DATABASE_URL?
    exit /b 1
)
echo [2] Import of file storage into the DB ^(safe to re-run^)
%PY% scripts\db\migrate_from_files.py
if errorlevel 1 (
    echo ERROR: migration reconciliation failed - see the table above. Service not started.
    exit /b 1
)
exit /b 0

:require_docker
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker not found. Install Docker Desktop.
    exit /b 1
)
docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker compose v2 plugin not found.
    exit /b 1
)
exit /b 0

:require_venv
if not exist "%PY%" (
    echo ERROR: venv not found. Run install.bat ^(or install_offline.bat^) first.
    exit /b 1
)
exit /b 0

:: read_env KEY DEFAULT OUTVAR - value of an active KEY= line in .env,
:: without a trailing comment; DEFAULT when the key is missing or empty.
:read_env
set "%~3=%~2"
set "_RV="
for /f "tokens=1,* delims==" %%a in ('findstr /B /C:"%~1=" .env 2^>nul') do set "_RV=%%b"
if defined _RV for /f "tokens=1 delims=# " %%v in ("!_RV!") do set "%~3=%%v"
exit /b 0

:: wait_health PORT - GET /health, 60 attempts x 2 s.
:wait_health
set "URL=http://localhost:%~1/health"
echo   Waiting for the API: !URL!
set "HAVE_CURL=0"
where curl.exe >nul 2>&1 && set "HAVE_CURL=1"
for /L %%n in (1,1,60) do (
    if "!HAVE_CURL!"=="1" (
        curl.exe -fsS -o nul --max-time 5 "!URL!" >nul 2>&1
    ) else (
        powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 '!URL!' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    )
    if not errorlevel 1 (
        echo   OK: API is up at !URL!
        exit /b 0
    )
    ping -n 3 127.0.0.1 >nul
)
echo ERROR: the API did not answer within 120 s.
echo        Docker: docker compose logs api.  Local: see the classifier-api window.
exit /b 1

:help
echo   start.cmd [docker]            whole stack in Docker (default)
echo   start.cmd local [--port N]    infrastructure in Docker, API and workers from venv
echo   start.cmd migrate             schema + file import only
echo   --training                    also start the fine-tuning worker
exit /b 0
