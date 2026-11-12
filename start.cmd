@echo off
setlocal enabledelayedexpansion

:: Start the appeals classifier (Windows). Linux analogue: start.sh
::
::   start.cmd [docker]            whole stack in Docker (default)
::   start.cmd local [--port N]    PostgreSQL + RabbitMQ in Docker, API and workers from venv
::                                 (--port is local only; docker mode uses HOST_PORT)
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
if defined PORT_ARG (
    call :read_env HOST_PORT 8010 HPORT
    echo WARNING: --port !PORT_ARG! is ignored in docker mode - the API port is HOST_PORT in .env ^(!HPORT!^).
)
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
call :resolve_urls || exit /b 1
echo [1/4] PostgreSQL and RabbitMQ
docker compose up -d --wait postgres rabbitmq
if errorlevel 1 (
    echo ERROR: postgres/rabbitmq did not become healthy. See: docker compose logs postgres rabbitmq
    exit /b 1
)
call :run_migrate 2 4 || exit /b 1

call :read_env API_PORT 8010 PORT
if defined PORT_ARG set "PORT=%PORT_ARG%"
call :read_env API_HOST 0.0.0.0 HOST
call :read_env CLASSIFY_WORKERS 2 WORKERS
set /a WORKERS=WORKERS+0
if !WORKERS! LSS 1 set "WORKERS=1"

:: Child windows inherit this environment (PYTHONPATH) and the working dir.
:: Window titles start with "classifier-": stop.cmd local closes them by title.
:: cmd /k keeps a window open after a crash so the error stays readable.
echo [4/4] API and !WORKERS! classify worker^(s^)
:: A busy API port usually means the Docker stack is running (its api publishes
:: HOST_PORT=8010) or an earlier start.cmd local: uvicorn would die in its window
:: and /health would be answered by the other service. Stop with a clear error.
netstat -ano -p TCP | findstr /R /C:"^ *TCP *[0-9.]*:!PORT! .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: port !PORT! is already in use.
    echo        Docker stack running? Stop it: stop.cmd   Earlier local start? stop.cmd local
    echo        Or use another port: start.cmd local --port N
    exit /b 1
)
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
call :run_migrate 1 2 || exit /b 1
echo   OK: migration finished
exit /b 0

:: ================================================================ helpers

:: run_migrate FIRST TOTAL - two steps (schema, import) numbered FIRST/TOTAL
:: and FIRST+1/TOTAL: local mode 2/4 and 3/4, migrate mode 1/2 and 2/2.
:run_migrate
set "S_NO=%~1"
set "S_TOTAL=%~2"
if not defined S_NO set "S_NO=1"
if not defined S_TOTAL set "S_TOTAL=2"
set "PYTHONPATH=%PROJECT%\src"
call :resolve_urls || exit /b 1
echo [!S_NO!/!S_TOTAL!] Database schema
%PY% scripts\db\init_db.py
if errorlevel 1 (
    echo ERROR: schema was not applied - is PostgreSQL reachable at DATABASE_URL?
    exit /b 1
)
set /a S_NO=S_NO+1
echo [!S_NO!/!S_TOTAL!] Import of file storage into the DB ^(skipped once done; --force to redo^)
%PY% scripts\db\migrate_from_files.py
if errorlevel 1 (
    echo ERROR: migration reconciliation failed - see the table above. Service not started.
    exit /b 1
)
exit /b 0

:: resolve_urls - DATABASE_URL / RABBITMQ_URL for local and migrate modes.
:: Not set (environment or .env): built from the compose credentials and the
:: host ports POSTGRES_PORT / RABBITMQ_PORT. Otherwise config would fall back
:: to localhost:5432/5672, where a native PostgreSQL/RabbitMQ may be running.
:: Set, but the port differs from POSTGRES_PORT / RABBITMQ_PORT: stop.
:: Child windows inherit the exported values. Passwords are never printed.
:resolve_urls
call :env_or_file POSTGRES_PORT "" PG_PORT_SET
call :env_or_file RABBITMQ_PORT "" MQ_PORT_SET
call :env_or_file DATABASE_URL "" DATABASE_URL
call :env_or_file RABBITMQ_URL "" RABBITMQ_URL
if not defined DATABASE_URL (
    set "_PORT=!PG_PORT_SET!"
    if not defined _PORT set "_PORT=5432"
    call :env_or_file POSTGRES_USER classifier _U
    call :env_or_file POSTGRES_PASSWORD classifier _P
    call :env_or_file POSTGRES_DB classifier _D
    set "DATABASE_URL=postgresql://!_U!:!_P!@localhost:!_PORT!/!_D!"
)
call :url_hostport DATABASE_URL 5432 DB_HOST DB_PORT
if defined PG_PORT_SET if not "!DB_PORT!"=="!PG_PORT_SET!" (
    echo ERROR: DATABASE_URL points to port !DB_PORT!, but POSTGRES_PORT=!PG_PORT_SET!.
    echo        Fix one of them in .env - otherwise another PostgreSQL would be used.
    exit /b 1
)
if not defined RABBITMQ_URL (
    set "_PORT=!MQ_PORT_SET!"
    if not defined _PORT set "_PORT=5672"
    call :env_or_file RABBITMQ_DEFAULT_USER classifier _U
    call :env_or_file RABBITMQ_DEFAULT_PASS classifier _P
    set "RABBITMQ_URL=amqp://!_U!:!_P!@localhost:!_PORT!//"
)
call :url_hostport RABBITMQ_URL 5672 MQ_HOST MQ_PORT
if defined MQ_PORT_SET if not "!MQ_PORT!"=="!MQ_PORT_SET!" (
    echo ERROR: RABBITMQ_URL points to port !MQ_PORT!, but RABBITMQ_PORT=!MQ_PORT_SET!.
    echo        Fix one of them in .env - otherwise another RabbitMQ would be used.
    exit /b 1
)
set "_U=" & set "_P=" & set "_D="
echo   PostgreSQL: !DB_HOST!:!DB_PORT!   RabbitMQ: !MQ_HOST!:!MQ_PORT!
exit /b 0

:: env_or_file KEY DEFAULT OUTVAR - environment value, else read_env.
:env_or_file
if defined %~1 (
    set "%~3=!%~1!"
    exit /b 0
)
call :read_env %~1 "%~2" %~3
exit /b 0

:: url_hostport URLVAR DEFAULT_PORT OUTHOST OUTPORT - host and port of
:: scheme://user:pass@host:port/... held in variable URLVAR.
:url_hostport
set "_HP=!%~1!"
set "_HP=!_HP:*://=!"
set "_HP=!_HP:*@=!"
for /f "tokens=1 delims=/" %%b in ("!_HP!") do set "_HP=%%b"
set "%~3=" & set "%~4=%~2"
for /f "tokens=1,2 delims=:" %%c in ("!_HP!") do (
    set "%~3=%%c"
    if not "%%d"=="" set "%~4=%%d"
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

:: wait_health PORT - GET /health, 60 attempts x 2 s. /health always answers
:: 200, so readiness is read from the body: "db":true and "broker":true
:: (findstr regex db.:true - the dot stands for the closing quote).
:wait_health
set "URL=http://localhost:%~1/health"
set "HFILE=%TEMP%\classifier-health-%RANDOM%.json"
echo   Waiting for the API: !URL!
set "HAVE_CURL=0"
where curl.exe >nul 2>&1 && set "HAVE_CURL=1"
set "H_SEEN=0"
set "H_DB=0"
set "H_MQ=0"
for /L %%n in (1,1,60) do (
    del "!HFILE!" >nul 2>&1
    if "!HAVE_CURL!"=="1" (
        curl.exe -fsS -o "!HFILE!" --max-time 5 "!URL!" >nul 2>&1
    ) else (
        powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 '!URL!').Content | Set-Content -Encoding ascii '!HFILE!'; exit 0 } catch { exit 1 }" >nul 2>&1
    )
    if exist "!HFILE!" (
        set "H_SEEN=1"
        set "H_DB=0"
        set "H_MQ=0"
        findstr /R /C:"db.:true" "!HFILE!" >nul 2>&1 && set "H_DB=1"
        findstr /R /C:"broker.:true" "!HFILE!" >nul 2>&1 && set "H_MQ=1"
        if "!H_DB!!H_MQ!"=="11" (
            del "!HFILE!" >nul 2>&1
            echo   OK: API is up, database and broker reachable: !URL!
            exit /b 0
        )
    )
    ping -n 3 127.0.0.1 >nul
)
del "!HFILE!" >nul 2>&1
if "!H_SEEN!"=="1" (
    if "!H_DB!"=="0" echo ERROR: the API cannot reach PostgreSQL ^(/health: db false^).
    if "!H_MQ!"=="0" echo ERROR: the API cannot reach RabbitMQ ^(/health: broker false^).
    echo        Docker: docker compose logs api.  Local: see the classifier-api window.
    exit /b 1
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
