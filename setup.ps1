# =============================================================================
# setup.ps1 -- Automatic installation of the Appeals Classification Agent
# =============================================================================
# Usage (from project root, in PowerShell):
#   .\setup.ps1
#   .\setup.ps1 -SkipVectorDB   # skip vector DB build if already exists
# =============================================================================

param(
    [switch]$SkipVectorDB
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Citizens Appeals Classification Agent -- Setup     " -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

# =============================================================================
# STEP 1: Check Python 3.11
# =============================================================================
Write-Host "`n[STEP 1] Checking Python 3.11..." -ForegroundColor Yellow

$python = $null

try {
    $ver = & py -3.11 --version 2>&1
    if ($ver -match "3\.11") { $python = "py -3.11"; Write-Host "   OK: $ver (via py launcher)" -ForegroundColor Green }
} catch {}

if (-not $python) {
    try {
        $ver = & python3.11 --version 2>&1
        if ($ver -match "3\.11") { $python = "python3.11"; Write-Host "   OK: $ver" -ForegroundColor Green }
    } catch {}
}

if (-not $python) {
    try {
        $ver = & python --version 2>&1
        if ($ver -match "3\.11") { $python = "python"; Write-Host "   OK: $ver" -ForegroundColor Green }
    } catch {}
}

if (-not $python) {
    Write-Host ""
    Write-Host "ERROR: Python 3.11 not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install Python 3.11 manually:" -ForegroundColor White
    Write-Host "  https://www.python.org/downloads/release/python-3119/" -ForegroundColor White
    Write-Host ""
    Write-Host "IMPORTANT: Check 'Add Python to PATH' during install." -ForegroundColor Yellow
    Write-Host "           Use version 3.11.x only (not 3.12, not 3.14)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "After install, restart PowerShell and run .\setup.ps1 again." -ForegroundColor White
    exit 1
}

# =============================================================================
# STEP 2: Create virtual environment
# =============================================================================
Write-Host "`n[STEP 2] Creating virtual environment (venv)..." -ForegroundColor Yellow

if (Test-Path "venv\Scripts\python.exe") {
    Write-Host "   OK: venv already exists, skipping" -ForegroundColor Green
} else {
    Invoke-Expression "$python -m venv venv"
    if (-not (Test-Path "venv\Scripts\python.exe")) {
        Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "   OK: venv created" -ForegroundColor Green
}

$venvPython = "venv\Scripts\python.exe"
$venvPip    = "venv\Scripts\pip.exe"

# =============================================================================
# STEP 3: Install dependencies
# =============================================================================
Write-Host "`n[STEP 3] Installing dependencies from requirements.txt..." -ForegroundColor Yellow

& $venvPip install --upgrade pip --quiet
& $venvPip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install dependencies. Check requirements.txt" -ForegroundColor Red
    exit 1
}
Write-Host "   OK: All dependencies installed" -ForegroundColor Green

# =============================================================================
# STEP 4: Configure .env
# =============================================================================
Write-Host "`n[STEP 4] Checking configuration (.env)..." -ForegroundColor Yellow

if (Test-Path ".env") {
    $envContent = Get-Content ".env" -Raw
    if ($envContent -match "GROQ_API_KEY\s*=\s*gsk_") {
        Write-Host "   OK: .env already configured (GROQ_API_KEY found)" -ForegroundColor Green
    } else {
        Write-Host "   WARN: .env found but GROQ_API_KEY may be missing" -ForegroundColor Yellow
    }
} else {
    Write-Host "   .env not found -- creating new one" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Get your free API key at: https://console.groq.com/keys" -ForegroundColor White
    Write-Host ""
    $apiKey = Read-Host "   Enter GROQ_API_KEY (starts with gsk_)"

    if (-not $apiKey -or -not $apiKey.StartsWith("gsk_")) {
        Write-Host "   WARN: Key not entered -- .env created with placeholder" -ForegroundColor Yellow
        $apiKey = "PASTE_YOUR_GROQ_API_KEY_HERE"
    }

    @"
GROQ_API_KEY=$apiKey
GROQ_MODEL=llama-3.3-70b-versatile
TOP_K_CANDIDATES=10
MIN_CONFIDENCE=0.65
FINETUNE_THRESHOLD=50
API_HOST=0.0.0.0
API_PORT=8000
"@ | Set-Content ".env" -Encoding UTF8

    Write-Host "   OK: .env created" -ForegroundColor Green
}

# =============================================================================
# STEP 5: Build vector database
# =============================================================================
if ($SkipVectorDB) {
    Write-Host "`n[STEP 5] Skipped (-SkipVectorDB flag)" -ForegroundColor Yellow
} else {
    Write-Host "`n[STEP 5] Building vector database (5-15 min first time)..." -ForegroundColor Yellow

    if (Test-Path "data\vector_db\embeddings.npy") {
        Write-Host "   OK: Vector database already exists, skipping" -ForegroundColor Green
    } else {
        Write-Host "   Loading multilingual-e5-base (~435 MB) and vectorizing 2108 entries..." -ForegroundColor White
        Write-Host "   This may take several minutes on first run." -ForegroundColor Gray
        Write-Host ""
        & $venvPython src/build_vectordb.py
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to build vector database" -ForegroundColor Red
            exit 1
        }
        Write-Host "   OK: Vector database built" -ForegroundColor Green
    }
}

# =============================================================================
# DONE
# =============================================================================
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "  Setup complete! Double-click launch.bat to start.  " -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Modes available in launch.bat:" -ForegroundColor White
Write-Host "    [1] Run tests" -ForegroundColor Gray
Write-Host "    [2] Classify manually" -ForegroundColor Gray
Write-Host "    [3] Start API server (port 8000)" -ForegroundColor Gray
Write-Host "    [4] Operator mode (verify + fine-tuning)" -ForegroundColor Gray
Write-Host ""
