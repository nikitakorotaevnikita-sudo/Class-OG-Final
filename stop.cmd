@echo off
setlocal

:: Stop the appeals classifier (Windows). Linux analogue: stop.sh
::
::   stop.cmd [all]     close the API and worker windows of this project, then
::                      stop the project containers (default; "local" and "api"
::                      do the same - compose stop keeps containers and data)
::   stop.cmd worker    close only the worker windows of this project folder
::                      (worker machine: nothing else is touched)
::   stop.cmd docker    stop and remove the stack containers
::
:: The pgdata volume is NEVER removed here: it holds appeal texts (personal data
:: under 59-FZ), results, verifications and the model registry. That is why the
:: -v switch of compose down is not used. Remove the volume only by hand
:: (docker volume rm) and only after the data has been exported.
::
:: ASCII-only on purpose: cmd.exe reads this file in the console code page.

cd /d "%~dp0"
set "PROJ=%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=all"

if /i "%MODE%"=="all"    goto :local
if /i "%MODE%"=="local"  goto :local
if /i "%MODE%"=="api"    goto :local
if /i "%MODE%"=="worker" goto :worker
if /i "%MODE%"=="docker" goto :docker
if /i "%MODE%"=="-h"     goto :help
if /i "%MODE%"=="--help" goto :help
echo Unknown mode: %MODE%  ^(all, api, worker or docker^)
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

:: stop_procs - Python processes of THIS project whose command line matches
:: the regex in PAT: the executable path or the command line lies under the
:: project folder. Window titles are not reliable: an elevated console
:: prefixes them ("Administrator: classifier-api"). /T also ends children
:: (the venv launcher starts the real interpreter). The "cmd /k" window of
:: each process is closed by parent PID, whatever its title.
:: A job killed mid-classification is not lost: when its lease expires,
:: another worker takes it. $root ends with a backslash, so D:\proj does not
:: match D:\proj-old\...
:stop_procs
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:PROJ.TrimEnd('\') + '\'; $all = Get-CimInstance Win32_Process; $all | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match $env:PAT -and ((($_.ExecutablePath) -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) -or $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) } | ForEach-Object { $p = $_; $win = $all | Where-Object { $_.ProcessId -eq $p.ParentProcessId -and $_.Name -eq 'cmd.exe' -and $_.CommandLine -match '/k ' }; taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null; Write-Output ('  stopped PID ' + $p.ProcessId); if ($win) { taskkill /F /PID $win.ProcessId 2>$null | Out-Null; Write-Output ('  closed window PID ' + $win.ProcessId) } }"
exit /b 0

:local
:: 1) API (uvicorn api.api_server:app) and queue workers (worker.run, also
::    started as src\worker\run.py) of this project folder.
set "PAT=worker\.run|worker\\run\.py|api\.api_server:app|uvicorn"
call :stop_procs
:: 2) Windows left from older runs (titles start with "classifier-").
taskkill /F /T /FI "WINDOWTITLE eq classifier-*" >nul 2>&1
echo   OK: classifier processes and windows closed ^(if any were open^).
:: 3) Project containers: stop, not down - containers and the data volume
::    stay. Covers PostgreSQL of all/api and a stack from start.cmd docker.
where docker >nul 2>&1
if not errorlevel 1 (
    docker compose --profile training stop
)
exit /b 0

:worker
:: Only the queue workers of this project folder; the API, other projects
:: and containers are left alone (no title-based fallback: a title says
:: nothing about the folder).
set "PAT=worker\.run|worker\\run\.py"
call :stop_procs
echo   OK: worker processes of this folder stopped ^(if any were running^).
exit /b 0

:help
echo   stop.cmd [all]     close API and worker windows, stop the project containers
echo   stop.cmd worker    close only the worker windows of this project folder
echo   stop.cmd docker    stop and remove the stack containers
exit /b 0
