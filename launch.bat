@echo off
setlocal enabledelayedexpansion

:: Launcher for the appeals classifier (Windows). Linux analogue: launch.sh
::
::   launch.bat                menu
::   launch.bat server [port]  local stack via start.cmd local (default 8010)
::   launch.bat check          verify the installation
::   launch.bat tests          test suite
::   launch.bat operator       operator mode (verification)
::   launch.bat classify       one-off classification from the console
::
:: Port precedence: command-line argument, then API_PORT in .env, then 8010.
:: 8010 is what docker-compose publishes, what the installers suggest and what
:: RX is pointed at on the stand, so the three stay in step.

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
cd /d "%PROJECT%"

set "DEFAULT_PORT=8010"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv not found. Run install.bat ^(or install_offline.bat^) first.
    pause
    exit /b 1
)
set "PY=venv\Scripts\python.exe"

:: Port fixed by the operator in .env, if any.
set "PORT=%DEFAULT_PORT%"
for /f "tokens=2 delims==" %%a in ('findstr /R "^API_PORT=" .env 2^>nul') do set "PORT=%%a"
for /f "tokens=* delims= " %%p in ("!PORT!") do set "PORT=%%p"
if "!PORT!"=="" set "PORT=%DEFAULT_PORT%"

:: Current LLM provider from .env - shown both in the menu and on startup.
set "CURRENT_LLM=groq"
for /f "tokens=2 delims==" %%a in ('findstr /R "^LLM_PROVIDER=" .env 2^>nul') do set "CURRENT_LLM=%%a"

:: Command-line mode: launch.bat <command> [port]
if not "%~1"=="" (
    if /i "%~1"=="server" (
        if not "%~2"=="" set "PORT=%~2"
        goto :server
    )
    if /i "%~1"=="check"    goto :check
    if /i "%~1"=="tests"    goto :tests
    if /i "%~1"=="operator" goto :operator
    if /i "%~1"=="classify" goto :classify
    if /i "%~1"=="-h"       goto :help
    if /i "%~1"=="--help"   goto :help
    echo Unknown command: %~1  ^(see --help^)
    exit /b 2
)

:menu
set "INTERACTIVE=1"
echo.
echo ================================================
echo   Citizens Appeals Classification Agent
echo ================================================
echo   [1] Start API server (port !PORT!)
echo   [2] Start API server on another port
echo   [3] Verify the installation
echo   [4] Run tests
echo   [5] Classify manually (type appeal text)
echo   [6] Operator mode  (verify + fine-tuning)
echo   [7] Select LLM provider
echo ================================================

echo   Current LLM: !CURRENT_LLM!
echo.

set "CHOICE="
set /p CHOICE=Select (1-7) [1]:

:: Empty input means [1], as in launch.sh. Without this the menu loops:
:: at end of input "set /p" leaves CHOICE empty and we jump back here.
if "%CHOICE%"=="" goto :server
if "%CHOICE%"=="1" goto :server
if "%CHOICE%"=="2" goto :ask_port
if "%CHOICE%"=="3" goto :check
if "%CHOICE%"=="4" goto :tests
if "%CHOICE%"=="5" goto :classify
if "%CHOICE%"=="6" goto :operator
if "%CHOICE%"=="7" goto :select_llm
goto :menu

:ask_port
:: A "set /p" inside an if (...) block does not hand its value out - the port
:: typed there was silently dropped and the service came up on the old one.
set "ASK_PORT="
set /p ASK_PORT=Port [!PORT!]:
if not "!ASK_PORT!"=="" set "PORT=!ASK_PORT!"

:server
:: Classifier vectors live in PostgreSQL now and classification runs in Celery
:: workers, so "server" starts the whole local stack through start.cmd local:
:: PostgreSQL + RabbitMQ in Docker, migration, API window and worker windows.
echo.
echo   NOTE: "server" now runs "start.cmd local" ^(API + workers, DB in Docker^).
echo   LLM provider: !CURRENT_LLM!
echo.
call "%PROJECT%\start.cmd" local --port !PORT!
goto :end

:check
%PY% scripts\check_offline.py --stage post --online
goto :end

:tests
%PY% -m pytest tests/ -q --ignore=tests/e2e
goto :end

:operator
%PY% src\operator_cli.py
goto :end

:classify
%PY% src\classify_manual.py
goto :end

:help
echo   launch.bat                menu
echo   launch.bat server [port]  local stack via start.cmd local (default %DEFAULT_PORT%)
echo   launch.bat check          verify the installation
echo   launch.bat tests          test suite
echo   launch.bat operator       operator mode
echo   launch.bat classify       one-off classification
exit /b 0

:select_llm
echo.
echo ================================================
echo   Select LLM provider
echo ================================================
echo   [1] ario    - Directum360, the default for this project
echo   [2] custom  - the customer's own OpenAI-compatible endpoint (vLLM)
echo   [3] ollama  - a local model
echo   [4] groq    - cloud, needs internet
echo   [5] gemini  - cloud, needs internet
echo ================================================
echo   Model name and base URL live in .env or in the back office
echo   (Settings tab). This menu only switches the provider.
echo.

set /p MODEL_CHOICE=Select (1-5):

if "%MODEL_CHOICE%"=="1" set "NEW_LLM=ario"   & goto :update_env
if "%MODEL_CHOICE%"=="2" set "NEW_LLM=custom" & goto :update_env
if "%MODEL_CHOICE%"=="3" set "NEW_LLM=ollama" & goto :update_env
if "%MODEL_CHOICE%"=="4" set "NEW_LLM=groq"   & goto :update_env
if "%MODEL_CHOICE%"=="5" set "NEW_LLM=gemini" & goto :update_env
echo Invalid choice.
goto :select_llm

:update_env
findstr /R "^LLM_PROVIDER=" .env >nul 2>&1
if !errorlevel!==0 (
    powershell -NoProfile -Command "(Get-Content .env) -replace '^LLM_PROVIDER=.*','LLM_PROVIDER=%NEW_LLM%' | Set-Content .env"
) else (
    echo LLM_PROVIDER=%NEW_LLM%>> .env
)
set "CURRENT_LLM=%NEW_LLM%"
echo.
echo   LLM provider set to: %NEW_LLM%
echo.
goto :menu

:end
:: pause only when a human is at the keyboard - "launch.bat server" called
:: from another script would otherwise hang on "press any key".
if defined INTERACTIVE (
    echo.
    pause
)
