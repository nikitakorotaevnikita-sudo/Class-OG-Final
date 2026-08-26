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
# STEP 1: Check Python (3.11 or 3.13 are both supported)
# =============================================================================
Write-Host "`n[STEP 1] Checking Python (3.11 / 3.12 / 3.13)..." -ForegroundColor Yellow

$python = $null
$supported = "3\.(11|12|13)"

foreach ($tag in @("3.13", "3.12", "3.11")) {
    if ($python) { break }
    try {
        $ver = & py "-$tag" --version 2>&1
        if ($ver -match $supported) { $python = "py -$tag"; Write-Host "   OK: $ver (via py launcher)" -ForegroundColor Green }
    } catch {}
}

foreach ($exe in @("python3.13", "python3.12", "python3.11", "python")) {
    if ($python) { break }
    try {
        $ver = & $exe --version 2>&1
        if ($ver -match $supported) { $python = $exe; Write-Host "   OK: $ver" -ForegroundColor Green }
    } catch {}
}

if (-not $python) {
    Write-Host ""
    Write-Host "ERROR: no supported Python found (need 3.11, 3.12 or 3.13)." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install one of these:" -ForegroundColor White
    Write-Host "  https://www.python.org/downloads/release/python-31315/" -ForegroundColor White
    Write-Host "  https://www.python.org/downloads/release/python-31210/" -ForegroundColor White
    Write-Host "  https://www.python.org/downloads/release/python-3119/" -ForegroundColor White
    Write-Host ""
    Write-Host "IMPORTANT: Check 'Add Python to PATH' during install." -ForegroundColor Yellow
    Write-Host "           Supported: 3.11.x, 3.12.x, or 3.13.x" -ForegroundColor Yellow
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
Write-Host "`n[STEP 4] Configuring .env..." -ForegroundColor Yellow

# Ask for LLM provider
Write-Host ""
Write-Host "   Select LLM provider:" -ForegroundColor White
Write-Host "   [1] Groq         - llama-3.3-70b-versatile (recommended, free)" -ForegroundColor Gray
Write-Host "   [2] Gemini       - gemini-2.5-flash (20 req/day free)" -ForegroundColor Gray
Write-Host "   [3] Ollama/Qwen  - qwen2.5-14b (local, no limits)" -ForegroundColor Gray
Write-Host "   [4] Ario         - Qwen3.6-35B-A3B (Directum360)" -ForegroundColor Gray
Write-Host ""

$providerChoice = Read-Host "   Enter (1/2/3/4)"

if ($providerChoice -eq "2") {
    $llmProvider = "gemini"
    Write-Host "   Selected: Gemini" -ForegroundColor Cyan
} elseif ($providerChoice -eq "3") {
    $llmProvider = "ollama"
    Write-Host "   Selected: Ollama/Qwen" -ForegroundColor Cyan
} elseif ($providerChoice -eq "4") {
    $llmProvider = "ario"
    Write-Host "   Selected: Ario" -ForegroundColor Cyan
} else {
    $llmProvider = "groq"
    Write-Host "   Selected: Groq" -ForegroundColor Cyan
}

# Ask for API key based on provider
$apiKey = ""
$geminiKey = ""
$arioKey = ""
$arioUrl = "https://llm.ario.directum360.ru/v1"

if ($llmProvider -eq "groq") {
    Write-Host ""
    Write-Host "   Get your free Groq API key at: https://console.groq.com/keys" -ForegroundColor White
    $apiKey = Read-Host "   Enter GROQ_API_KEY (starts with gsk_)"
    if (-not $apiKey -or -not $apiKey.StartsWith("gsk_")) {
        Write-Host "   WARN: Key not entered -- using placeholder" -ForegroundColor Yellow
        $apiKey = "PASTE_YOUR_GROQ_API_KEY_HERE"
    }
} elseif ($llmProvider -eq "gemini") {
    Write-Host ""
    Write-Host "   Get your free Gemini API key at: https://ai.google.dev/" -ForegroundColor White
    $geminiKey = Read-Host "   Enter GEMINI_API_KEY (starts with AIza)"
    if (-not $geminiKey -or -not $geminiKey.StartsWith("AIza")) {
        Write-Host "   WARN: Key not entered -- using placeholder" -ForegroundColor Yellow
        $geminiKey = "PASTE_YOUR_GEMINI_API_KEY_HERE"
    }
} elseif ($llmProvider -eq "ario") {
    Write-Host ""
    Write-Host "   Enter your Ario API token:" -ForegroundColor White
    $arioKey = Read-Host "   ARIO_API_KEY"
    if (-not $arioKey) {
        Write-Host "   WARN: Key not entered -- using placeholder" -ForegroundColor Yellow
        $arioKey = "PASTE_YOUR_ARIO_API_KEY_HERE"
    }
    $inputUrl = Read-Host "   ARIO_BASE_URL [$arioUrl]"
    if ($inputUrl) { $arioUrl = $inputUrl }
} else {
    Write-Host ""
    Write-Host "   Ollama selected. Install: https://ollama.com" -ForegroundColor White
    Write-Host "   Then run: ollama pull qwen2.5-14b" -ForegroundColor White
}

# Write .env file
$envContent = @"
GROQ_API_KEY=$apiKey
LLM_PROVIDER=$llmProvider
GROQ_MODEL=llama-3.3-70b-versatile
GEMINI_API_KEY=$geminiKey
OLLAMA_MODEL=qwen2.5-14b
OLLAMA_BASE_URL=http://localhost:11434/v1
ARIO_API_KEY=$arioKey
ARIO_BASE_URL=$arioUrl
ARIO_MODEL=Qwen/Qwen3.6-35B-A3B
TOP_K_CANDIDATES=10
MIN_CONFIDENCE=0.65
ENABLE_HEURISTIC_RERANKER=false
FINETUNE_THRESHOLD=50
API_HOST=0.0.0.0
API_PORT=8000
BACKOFFICE_USER=admin
BACKOFFICE_PASSWORD=password
RX_ODATA_URL=http://172.16.96.98/integration/odata
RX_USER=Administrator
RX_PASSWORD=11111
ENABLE_REPEAT_DETECTION=false
ENABLE_WITHDRAWAL_DETECTION=false
"@

$envContent | Set-Content ".env" -Encoding UTF8
Write-Host "   OK: .env created with provider: $llmProvider" -ForegroundColor Green

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
