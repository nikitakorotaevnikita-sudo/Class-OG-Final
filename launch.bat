@echo off
setlocal enabledelayedexpansion

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"

cd /d "%PROJECT%"

if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv not found. Run setup.ps1 first.
    pause
    exit /b 1
)

if not exist "data\vector_db\embeddings.npy" (
    echo ERROR: Vector DB not found. Run setup.ps1 first.
    pause
    exit /b 1
)

:menu
echo.
echo ================================================
echo   Citizens Appeals Classification Agent
echo ================================================
echo   [1] Run tests
echo   [2] Classify manually (type appeal text)
echo   [3] Start API server (port 8000)
echo   [4] Operator mode  (verify + fine-tuning)
echo   [5] Select LLM model
echo ================================================
echo.

:: Get current LLM provider from .env
set "CURRENT_LLM=groq"
if exist ".env" (
    findstr /C:"LLM_PROVIDER" .env >nul 2>&1
    if !errorlevel!==0 (
        for /f "tokens=2 delims==" %%a in ('findstr "LLM_PROVIDER" .env') do set "CURRENT_LLM=%%a"
    )
)
echo   Current LLM: !CURRENT_LLM!
echo.

set /p CHOICE=Select (1/2/3/4/5):

if "%CHOICE%"=="1" (
    venv\Scripts\python.exe src/test_agent.py
    goto :end
)
if "%CHOICE%"=="2" (
    venv\Scripts\python.exe src/classify_manual.py
    goto :end
)
if "%CHOICE%"=="3" (
    venv\Scripts\python.exe -m uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload
    goto :end
)
if "%CHOICE%"=="4" (
    venv\Scripts\python.exe src/operator_cli.py
    goto :end
)
if "%CHOICE%"=="5" goto :select_llm

goto :menu

:select_llm
echo.
echo ================================================
echo   Select LLM Model
echo ================================================
echo   [1] Groq         - llama-3.3-70b-versatile
echo   [2] Gemini       - gemini-2.5-flash (20 req/day)
echo   [3] Qwen (Ollama) - qwen2.5-14b (local, no limits)
echo ================================================
echo.

set /p MODEL_CHOICE=Select (1/2/3):

if "%MODEL_CHOICE%"=="1" (
    set "NEW_LLM=groq"
    goto :update_env
)
if "%MODEL_CHOICE%"=="2" (
    set "NEW_LLM=gemini"
    goto :update_env
)
if "%MODEL_CHOICE%"=="3" (
    set "NEW_LLM=ollama"
    goto :update_env
)
echo Invalid choice.
goto :select_llm

:update_env
:: Update or add LLM_PROVIDER in .env
findstr /C:"LLM_PROVIDER" .env >nul 2>&1
if !errorlevel!==0 (
    :: LLM_PROVIDER exists, replace it
    powershell -Command "(Get-Content .env) -replace 'LLM_PROVIDER=.*','LLM_PROVIDER=%NEW_LLM%' | Set-Content .env"
) else (
    :: LLM_PROVIDER not found, add it
    echo LLM_PROVIDER=%NEW_LLM%>> .env
)
echo.
echo   LLM model set to: %NEW_LLM%
echo.
goto :menu

:end
echo.
pause
