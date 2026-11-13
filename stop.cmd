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
:: 1) Python processes of THIS project: API (uvicorn api.api_server:app) and
::    Celery workers (queueing.celery_app) whose executable path or command
::    line lies under the project folder. Window titles are not reliable: an
::    elevated console prefixes them ("Administrator: classifier-api").
::    /T also ends children (the venv launcher starts the real interpreter).
::    A task killed mid-classification is not lost: acks_late re-queues it.
::    $root ends with a backslash, so D:\proj does not match D:\proj-old\...
set "PROJ=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PROJ.TrimEnd('\') + '\'; $all = Get-CimInstance Win32_Process; $all | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'queueing\.celery_app|api\.api_server:app|uvicorn' -and ((($_.ExecutablePath) -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) -or $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) } | ForEach-Object { $p = $_; $win = $all | Where-Object { $_.ProcessId -eq $p.ParentProcessId -and $_.Name -eq 'cmd.exe' -and $_.CommandLine -match '/k ' }; taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null; Write-Output ('  stopped PID ' + $p.ProcessId); if ($win) { taskkill /F /PID $win.ProcessId 2>$null | Out-Null; Write-Output ('  closed window PID ' + $win.ProcessId) } }"
:: 2) Windows left from older runs (titles start with "classifier-"). The
::    "cmd /k" window of each process stopped above is closed by parent PID,
::    whatever the console prefixes to its title ("Administrator: ...").
taskkill /F /T /FI "WINDOWTITLE eq classifier-*" >nul 2>&1
echo   OK: classifier processes and windows closed ^(if any were open^).
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
