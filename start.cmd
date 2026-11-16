@echo off
setlocal enabledelayedexpansion

:: Start the appeals classifier (Windows). Linux analogue: start.sh
::
::   start.cmd [all] [--port N]     PostgreSQL in Docker, API and workers from venv
::                                  (default; "local" is the old name of this mode)
::   start.cmd api [--port N]       PostgreSQL in Docker, API from venv - no workers
::                                  (the workers run on other machines)
::   start.cmd worker               workers only; the DB is DATABASE_URL from .env
::   start.cmd docker [--no-workers]  whole stack in Docker (--no-workers: postgres,
::                                  migrate and api only; the API port is HOST_PORT)
::   start.cmd migrate              schema + file import only (the DB must be running)
::
::   --training                     also start the fine-tuning worker (all, worker, docker)
::
:: Stop: stop.cmd [all, api, worker or docker]. This file stays ASCII-only:
:: cmd.exe reads it in the console code page (CP866/CP1251) and breaks on UTF-8.

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
cd /d "%PROJECT%"

set "PY=venv\Scripts\python.exe"
set "MODE=all"
set "TRAINING=0"
set "NO_WORKERS=0"
set "PORT_ARG="

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="all"        ( set "MODE=all"     & goto :next )
if /i "%~1"=="local"      ( set "MODE=all"     & goto :next )
if /i "%~1"=="api"        ( set "MODE=api"     & goto :next )
if /i "%~1"=="worker"     ( set "MODE=worker"  & goto :next )
if /i "%~1"=="docker"     ( set "MODE=docker"  & goto :next )
if /i "%~1"=="migrate"    ( set "MODE=migrate" & goto :next )
if /i "%~1"=="--training" ( set "TRAINING=1"   & goto :next )
if /i "%~1"=="--no-workers" ( set "NO_WORKERS=1" & goto :next )
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

if "%NO_WORKERS%"=="1" if /i not "%MODE%"=="docker" (
    echo WARNING: --no-workers applies to docker mode only ^(no venv workers: start.cmd api^).
)

if /i "%MODE%"=="all"     goto :local
if /i "%MODE%"=="api"     goto :local
if /i "%MODE%"=="worker"  goto :worker
if /i "%MODE%"=="docker"  goto :docker
if /i "%MODE%"=="migrate" goto :migrate_only
exit /b 2

:: ---------------------------------------------------------------- docker
:docker
call :require_docker || exit /b 1
set "PROFILE="
if "%TRAINING%"=="1" set "PROFILE=--profile training"
set "NEED_AGENT=1"
echo.
echo === Starting the stack in Docker ===
if defined PORT_ARG (
    call :read_env HOST_PORT 8010 HPORT
    echo WARNING: --port !PORT_ARG! is ignored in docker mode - the API port is HOST_PORT in .env ^(!HPORT!^).
)
if "%NO_WORKERS%"=="1" (
    if "%TRAINING%"=="1" echo WARNING: --training is ignored together with --no-workers.
    set "NEED_AGENT=0"
    docker compose up -d --build postgres migrate api
) else (
    docker compose %PROFILE% up -d --build
)
if errorlevel 1 (
    echo ERROR: docker compose up failed. See: docker compose logs migrate api
    exit /b 1
)
call :read_env HOST_PORT 8010 PORT
call :wait_health !PORT! !NEED_AGENT! || exit /b 1
echo   Back office: http://localhost:!PORT!/backoffice
exit /b 0

:: ---------------------------------------------------------------- all / api
:: all: PostgreSQL in Docker, migration, API and workers from venv.
:: api: the same without workers - they run on other machines (start.cmd worker).
:local
call :require_docker || exit /b 1
call :require_venv || exit /b 1
echo.
if /i "%MODE%"=="all" (
    echo === Everything: PostgreSQL in Docker, API and workers from venv ===
) else (
    echo === Without workers: PostgreSQL in Docker, API from venv ===
    if "%TRAINING%"=="1" echo WARNING: --training is ignored in api mode ^(no workers^).
)
call :resolve_urls || exit /b 1
echo [1/4] PostgreSQL
docker compose up -d --wait postgres
if errorlevel 1 (
    echo ERROR: postgres did not become healthy. See: docker compose logs postgres
    exit /b 1
)
call :run_migrate 2 4 || exit /b 1

call :read_env API_PORT 8010 PORT
if defined PORT_ARG set "PORT=%PORT_ARG%"
call :read_env API_HOST 0.0.0.0 HOST

:: Child windows inherit this environment (PYTHONPATH) and the working dir;
:: the database address they build from .env themselves (see resolve_urls).
:: Window titles start with "classifier-": stop.cmd closes them.
:: cmd /k keeps a window open after a crash so the error stays readable.
if /i "%MODE%"=="all" (
    echo [4/4] API and workers
) else (
    echo [4/4] API
)
:: A busy API port usually means the Docker stack is running (its api publishes
:: HOST_PORT=8010) or an earlier start.cmd: uvicorn would die in its window
:: and /health would be answered by the other service. Stop with a clear error.
netstat -ano -p TCP | findstr /R /C:"^ *TCP *[0-9.]*:!PORT! .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: port !PORT! is already in use.
    echo        Docker stack running? Stop it: stop.cmd docker   Earlier start? stop.cmd
    echo        Or use another port: start.cmd !MODE! --port N
    exit /b 1
)
start "classifier-api" /D "%PROJECT%" cmd /k %PY% -m uvicorn api.api_server:app --app-dir src --host !HOST! --port !PORT!
set "NEED_AGENT=0"
if /i "%MODE%"=="all" (
    set "NEED_AGENT=1"
    call :start_workers
)
call :wait_health !PORT! !NEED_AGENT! || exit /b 1
echo   Back office: http://localhost:!PORT!/backoffice    Stop: stop.cmd !MODE!
exit /b 0

:: ---------------------------------------------------------------- worker
:: Workers only: no Docker, no migration, no API. The database address is
:: in .env of THIS machine (or the environment), usually another host. This
:: file never reads or prints it (cmd would mangle "!" in a password): the
:: Python helper and the workers read it through core.config themselves.
:: The POSTGRES_PORT cross-check of resolve_urls does not apply here.
:worker
call :require_venv || exit /b 1
echo.
echo === Workers only: the database is on the main server ===
if defined PORT_ARG echo WARNING: --port !PORT_ARG! is ignored in worker mode - workers serve no HTTP port.
set "PYTHONPATH=%PROJECT%\src"
%PY% scripts\check_db.py check
if errorlevel 1 (
    echo ERROR: the database check failed - see the reason above. Fix the database
    echo        address in .env of this machine: postgresql://user:password@host:port/db
    exit /b 1
)
call :start_workers
echo   Worker windows: classifier-worker-*    Stop: stop.cmd worker
exit /b 0

:: ---------------------------------------------------------------- migrate
:migrate_only
call :require_venv || exit /b 1
call :run_migrate 1 2 || exit /b 1
echo   OK: migration finished
exit /b 0

:: ================================================================ helpers

:: start_workers - CLASSIFY_WORKERS windows of the classification worker (one
:: process runs one job at a time and loads the model once), plus the
:: fine-tuning worker with --training. Titles: classifier-worker-N.
:: Workers of this folder that are already running (an earlier start.cmd
:: worker or all) are not stopped: a warning says how to avoid duplicates.
:start_workers
set "PYTHONPATH=%PROJECT%\src"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PROJECT.TrimEnd('\') + '\'; $n = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'worker\.run|worker\\run\.py' -and ((($_.ExecutablePath) -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) -or $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) }).Count; if ($n -gt 0) { Write-Output ('  WARNING: ' + $n + ' worker process(es) of this folder are already running - new ones are added. To restart cleanly: stop.cmd worker, then start again.') }"
call :read_env CLASSIFY_WORKERS 2 WORKERS
set /a WORKERS=WORKERS+0
if !WORKERS! LSS 1 set "WORKERS=1"
echo   !WORKERS! classify worker^(s^)
for /L %%i in (1,1,!WORKERS!) do (
    start "classifier-worker-%%i" /D "%PROJECT%" cmd /k %PY% -m worker.run --queues classify_sync,classify
)
if "%TRAINING%"=="1" (
    start "classifier-worker-training" /D "%PROJECT%" cmd /k %PY% -m worker.run --queues training
)
exit /b 0

:: run_migrate FIRST TOTAL - two steps (schema, import) numbered FIRST/TOTAL
:: and FIRST+1/TOTAL: all/api mode 2/4 and 3/4, migrate mode 1/2 and 2/2.
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

:: resolve_urls - database address for all, api and migrate modes. This
:: file never builds, reads or exports DATABASE_URL (cmd would mangle "!",
:: and a password may contain any characters): the API, workers and migration
:: build it themselves in Python (core.config: DATABASE_URL from .env, else
:: POSTGRES_USER/PASSWORD/DB/PORT on localhost, percent-encoded).
:: scripts\check_db.py resolve prints host:port of that address and stops
:: (exit 2) when DATABASE_URL and POSTGRES_PORT disagree on the port -
:: otherwise another PostgreSQL would be used. Passwords are never printed.
:resolve_urls
set "PYTHONPATH=%PROJECT%\src"
%PY% scripts\check_db.py resolve
if errorlevel 1 exit /b 1
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
:: The CLI is there, but the engine may be stopped or hung (Docker Desktop
:: then answers "Internal Server Error for API route ..."): say so plainly.
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: the Docker engine does not respond. Start Docker Desktop, or if it
    echo        is already running restart it ^(Troubleshoot - Restart^), wait for
    echo        "Engine running" and run this command again.
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

:: wait_health PORT NEED_AGENT - GET /health every 2 s. /health always answers
:: 200, so readiness is read from the body (findstr regex db.:true - the dot
:: stands for the closing quote):
::   NEED_AGENT=1 (all, docker): "db":true and "agent_ready":true - a classify
::                worker loaded the model and listens; 150 attempts (300 s).
::   NEED_AGENT=0 (api, docker --no-workers): "db":true only, 60 attempts,
::                then a warning that no workers are running.
:wait_health
set "URL=http://localhost:%~1/health"
set "H_NEED=%~2"
if not defined H_NEED set "H_NEED=1"
set "H_TRIES=60"
if "!H_NEED!"=="1" set "H_TRIES=150"
set /a H_SECS=H_TRIES*2
set "HFILE=%TEMP%\classifier-health-%RANDOM%.json"
echo   Waiting for the API: !URL!
set "HAVE_CURL=0"
where curl.exe >nul 2>&1 && set "HAVE_CURL=1"
set "H_SEEN=0"
set "H_DB=0"
set "H_AGENT=0"
for /L %%n in (1,1,!H_TRIES!) do (
    del "!HFILE!" >nul 2>&1
    if "!HAVE_CURL!"=="1" (
        curl.exe -fsS -o "!HFILE!" --max-time 5 "!URL!" >nul 2>&1
    ) else (
        powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 '!URL!').Content | Set-Content -Encoding ascii '!HFILE!'; exit 0 } catch { exit 1 }" >nul 2>&1
    )
    if exist "!HFILE!" (
        set "H_SEEN=1"
        set "H_DB=0"
        set "H_AGENT=0"
        findstr /R /C:"db.:true" "!HFILE!" >nul 2>&1 && set "H_DB=1"
        findstr /R /C:"agent_ready.:true" "!HFILE!" >nul 2>&1 && set "H_AGENT=1"
        if "!H_NEED!!H_DB!"=="01" (
            del "!HFILE!" >nul 2>&1
            echo   OK: API is up, database reachable: !URL!
            echo   WARNING: no workers are running - jobs wait in the queue until a
            echo            worker connects ^(start.cmd worker on a worker machine^).
            exit /b 0
        )
        if "!H_NEED!!H_DB!!H_AGENT!"=="111" (
            del "!HFILE!" >nul 2>&1
            echo   OK: API is up, database reachable, a classify worker is ready: !URL!
            exit /b 0
        )
    )
    ping -n 3 127.0.0.1 >nul
)
del "!HFILE!" >nul 2>&1
if "!H_SEEN!"=="1" (
    if "!H_DB!"=="0" echo ERROR: the API cannot reach PostgreSQL ^(/health: db false^).
    if "!H_NEED!!H_AGENT!"=="10" echo ERROR: no classify worker is ready ^(/health: agent_ready false^). See the classifier-worker-* windows or: docker compose logs worker-classify
    echo        Docker: docker compose logs api.  Local: see the classifier-api window.
    exit /b 1
)
echo ERROR: the API did not answer within !H_SECS! s.
echo        Docker: docker compose logs api.  Local: see the classifier-api window.
exit /b 1

:help
echo   start.cmd [all] [--port N]       PostgreSQL in Docker, API and workers from venv (default)
echo   start.cmd api [--port N]         PostgreSQL in Docker, API from venv, no workers
echo   start.cmd worker                 workers only, DB = DATABASE_URL in .env
echo   start.cmd docker [--no-workers]  whole stack in Docker
echo   start.cmd migrate                schema + file import only
echo   --training                       also start the fine-tuning worker
echo   "local" = "all" ^(old name^)
exit /b 0
