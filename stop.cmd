@echo off
setlocal

:: Stop the appeals classifier (Windows). Linux analogue: stop.sh
::
::   stop.cmd [docker]   stop and remove the stack containers (default)
::   stop.cmd local      close the classifier-* windows, then stop PostgreSQL and RabbitMQ
::
:: The pgdata volume is NEVER removed here: it holds appeal texts (personal data
:: under 59-FZ), results, verifications and the model registry. That is why the
:: -v switch of compose down is not used. Remove the volume only by hand
:: (docker volume rm) and only after the data has been exported.
::
:: ASCII-only on purpose: cmd.exe reads this file in the console code page.

cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=docker"

if /i "%MODE%"=="docker" goto :docker
if /i "%MODE%"=="local"  goto :local
if /i "%MODE%"=="-h"     goto :help
if /i "%MODE%"=="--help" goto :help
echo Unknown mode: %MODE%  ^(docker or local^)
exit /b 2

:docker
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker not found.
    exit /b 1
)
:: --profile training also stops the fine-tuning worker if it was started.
docker compose --profile training down
exit /b %errorlevel%

:local
:: start.cmd local gives every window a title starting with "classifier-".
:: /T also ends the python process started inside the window. A task killed
:: mid-classification is not lost: acks_late returns it to the queue.
taskkill /F /T /FI "WINDOWTITLE eq classifier-*"
echo   OK: classifier-* windows closed ^(if any were open^).
where docker >nul 2>&1
if not errorlevel 1 (
    rem stop, not down: containers and the data volume stay.
    docker compose stop postgres rabbitmq
)
exit /b 0

:help
echo   stop.cmd [docker]   stop and remove the stack containers (default)
echo   stop.cmd local      close the classifier-* windows, stop PostgreSQL and RabbitMQ
exit /b 0
