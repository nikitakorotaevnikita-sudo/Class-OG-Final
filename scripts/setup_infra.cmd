@echo off
setlocal

:: Infrastructure step of install.bat / install_offline.bat (Windows).
:: Linux analogue: setup_infra in scripts/install_common.sh.
::
::   scripts\setup_infra.cmd            pick ports, start PostgreSQL + RabbitMQ, migrate
::   scripts\setup_infra.cmd offline    same, but never pull images: if they are
::                                      missing, explain how to load them and skip
::
:: 1) scripts\setup_infra_env.py writes free host ports (POSTGRES_PORT,
::    RABBITMQ_PORT, RABBITMQ_MGMT_PORT) and matching DATABASE_URL /
::    RABBITMQ_URL into .env. Values already in .env are kept.
:: 2) docker compose up -d --wait postgres rabbitmq (waits for healthchecks).
:: 3) start.cmd migrate: schema + one-shot import (a rerun exits 0 by marker).
::
:: No Docker, or the engine is not running: NOT an install failure - a note to
:: run "start.cmd migrate" later (launch.bat server does it via start.cmd local).
:: Exit code: 0 done or skipped, 1 compose or migration failed.
:: ASCII-only on purpose: cmd.exe reads this file in the console code page.

cd /d "%~dp0.."
set "PROJECT=%CD%"
set "PY=venv\Scripts\python.exe"
set "MODE=%~1"

"%PY%" scripts\setup_infra_env.py
if errorlevel 1 (
    echo    WARNING: host ports were not chosen - set POSTGRES_PORT, RABBITMQ_PORT,
    echo             RABBITMQ_MGMT_PORT in .env by hand.
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo    NOTE: Docker not found - PostgreSQL and RabbitMQ were not started.
    echo          Install Docker Desktop.
    goto :later
)
docker info >nul 2>&1
if errorlevel 1 (
    echo    NOTE: the Docker engine is not running - PostgreSQL and RabbitMQ were not started.
    echo          Start Docker Desktop and wait until it says "Engine running".
    goto :later
)
docker compose version >nul 2>&1
if errorlevel 1 (
    echo    NOTE: docker compose v2 plugin not found - PostgreSQL and RabbitMQ were not started.
    goto :later
)

if /i not "%MODE%"=="offline" goto :up
docker image inspect pgvector/pgvector:pg16 rabbitmq:3.13-management >nul 2>&1
if not errorlevel 1 goto :up
echo    NOTE: images pgvector/pgvector:pg16 and rabbitmq:3.13-management are not
echo          loaded, and they cannot be pulled without internet. On a machine
echo          with internet:
echo            docker pull pgvector/pgvector:pg16
echo            docker pull rabbitmq:3.13-management
echo            docker save -o infra-images.tar pgvector/pgvector:pg16 rabbitmq:3.13-management
echo          Here:
echo            docker load -i infra-images.tar
goto :later

:up
echo    Starting PostgreSQL and RabbitMQ: docker compose up -d --wait postgres rabbitmq
docker compose up -d --wait postgres rabbitmq
if errorlevel 1 (
    echo    WARNING: PostgreSQL / RabbitMQ did not become healthy.
    echo             See: docker compose logs postgres rabbitmq
    echo             Then: start.cmd migrate
    exit /b 1
)
echo    OK: PostgreSQL and RabbitMQ are running
call "%PROJECT%\start.cmd" migrate
if errorlevel 1 (
    echo    WARNING: migration failed - see the output above. Retry: start.cmd migrate
    exit /b 1
)
echo    OK: database schema and data import are ready
exit /b 0

:later
echo    Later, once Docker is running: start.cmd migrate
echo    ^(or just launch.bat server - start.cmd local starts PostgreSQL and RabbitMQ itself^)
exit /b 0
