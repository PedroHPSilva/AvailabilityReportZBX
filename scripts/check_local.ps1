$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$BackendEnv = Join-Path $BackendDir ".env"
$FrontendEnv = Join-Path $FrontendDir ".env"

function Write-Check {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Message
    )

    if ($Ok) {
        Write-Host "[OK] $Name - $Message"
    } else {
        Write-Host "[ERRO] $Name - $Message"
    }
}

function Test-PortFree {
    param([int]$Port)

    $Connection = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -eq $Connection
}

$BackendEnvExists = Test-Path $BackendEnv
Write-Check "backend/.env" $BackendEnvExists "arquivo de configuração do backend"

if ($BackendEnvExists) {
    $BackendEnvContent = Get-Content $BackendEnv -Raw
    Write-Check "ZABBIX_URL" ($BackendEnvContent -match "(?m)^ZABBIX_URL=.+") "URL da API do Zabbix configurada"
}

Write-Check "frontend/.env" (Test-Path $FrontendEnv) "arquivo de configuração do frontend"
Write-Check "backend/requirements.txt" (Test-Path (Join-Path $BackendDir "requirements.txt")) "dependências Python declaradas"
Write-Check "frontend/package.json" (Test-Path (Join-Path $FrontendDir "package.json")) "dependências Node declaradas"
Write-Check "porta 8000" (Test-PortFree 8000) "porta padrão do backend livre"
Write-Check "porta 3000" (Test-PortFree 3000) "porta padrão do frontend livre"

function Test-PythonDependencies {
    $Candidates = @()
    $VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $Candidates += $VenvPython
    }
    $Candidates += "python"

    foreach ($Candidate in $Candidates) {
        try {
            & $Candidate -c "import fastapi, uvicorn, requests, dotenv" 2>$null
            if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
                return $true
            }
        } catch {
            continue
        }
    }

    return $false
}

$PythonDependenciesOk = Test-PythonDependencies
if ($PythonDependenciesOk) {
    Write-Check "dependências Python" $true "FastAPI, Uvicorn, Requests e python-dotenv disponíveis"
} else {
    Write-Check "dependências Python" $false "execute instalação em backend com pip install -r requirements.txt"
}

$NodeModules = Join-Path $FrontendDir "node_modules"
Write-Check "dependências Node" (Test-Path $NodeModules) "node_modules presente após npm install"

try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 3
    Write-Check "health backend" ($Health.status -eq "ok") "backend respondeu em http://127.0.0.1:8000/health"
} catch {
    Write-Check "health backend" $false "backend não está em execução ou não respondeu"
}
