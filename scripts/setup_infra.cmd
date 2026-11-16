@echo off
setlocal

:: Infrastructure step of install.bat / install_offline.bat (Windows).
:: Linux analogue: setup_infra in scripts/install_common.sh.
::
::   scripts\setup_infra.cmd            pick the port, start PostgreSQL, migrate
::   scripts\setup_infra.cmd offline    same, but never pull the image: if it is
::                                      missing, explain how to load it and skip
::
:: 1) scripts\setup_infra_env.py writes a free host port (POSTGRES_PORT) and a
::    matching DATABASE_URL into .env. Values already in .env are kept.
:: 2) docker compose up -d --wait postgres (waits for the healthcheck).
:: 3) start.cmd migrate: schema + one-shot import (a rerun exits 0 by marker).
::
:: No Docker, or the engine is not running: NOT an install failure - a note to
:: run "start.cmd migrate" later (launch.bat server does it via start.cmd all).
:: A worker-only machine does not run this step at all (the installers skip it).
:: Exit code: 0 done or skipped, 1 compose or migration failed.
:: ASCII-only on purpose: cmd.exe reads this file in the console code page.

cd /d "%~dp0.."
set "PROJECT=%CD%"
set "PY=venv\Scripts\python.exe"
set "MODE=%~1"

"%PY%" scripts\setup_infra_env.py
if errorlevel 1 (
    echo    WARNING: the host port was not chosen - see the error above, fix
    echo             POSTGRES_PORT in .env and rerun.
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo    NOTE: Docker not found - PostgreSQL was not started.
    echo          Install Docker Desktop.
    goto :later
)
docker info >nul 2>&1
if errorlevel 1 (
    echo    NOTE: the Docker engine is not running - PostgreSQL was not started.
    echo          Start Docker Desktop and wait until it says "Engine running".
    goto :later
)
docker compose version >nul 2>&1
if errorlevel 1 (
    echo    NOTE: docker compose v2 plugin not found - PostgreSQL was not started.
    goto :later
)

if /i not "%MODE%"=="offline" goto :up
docker image inspect pgvector/pgvector:pg16 >nul 2>&1
if not errorlevel 1 goto :up
echo    NOTE: image pgvector/pgvector:pg16 is not loaded, and it cannot be
echo          pulled without internet. On a machine with internet:
echo            docker pull pgvector/pgvector:pg16
echo            docker save -o infra-images.tar pgvector/pgvector:pg16
echo          Here:
echo            docker load -i infra-images.tar
goto :later

:up
echo    Starting PostgreSQL: docker compose up -d --wait postgres
docker compose up -d --wait postgres
if errorlevel 1 (
    echo    WARNING: PostgreSQL did not become healthy.
    echo             See: docker compose logs postgres
    echo             Then: start.cmd migrate
    exit /b 1
)
echo    OK: PostgreSQL is running
call "%PROJECT%\start.cmd" migrate
if errorlevel 1 (
    echo    WARNING: migration failed - see the output above. Retry: start.cmd migrate
    exit /b 1
)
echo    OK: database schema and data import are ready
exit /b 0

:later
echo    Later, once Docker is running: start.cmd migrate
echo    ^(or just launch.bat server - start.cmd all starts PostgreSQL itself^)
exit /b 0
