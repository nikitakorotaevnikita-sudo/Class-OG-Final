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

echo.
echo ================================================
echo   Citizens Appeals Classification Agent
echo ================================================
echo   [1] Run tests
echo   [2] Classify manually (type appeal text)
echo   [3] Start API server (port 8000)
echo   [4] Operator mode  (verify + fine-tuning)
echo ================================================
echo.
set /p CHOICE=Select (1/2/3/4):
echo.

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

venv\Scripts\python.exe src/operator_cli.py

:end
echo.
pause
