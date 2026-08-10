param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendDir = Join-Path $ProjectRoot "backend"
$LogDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $LogDir "backend.pid"
$LogFile = Join-Path $LogDir "backend.log"
$ErrorLogFile = Join-Path $LogDir "backend.error.log"

function Test-PortInUse {
    param([int]$LocalPort)

    $Connection = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    return $null -ne $Connection
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Backend já está em execução. PID: $ExistingPid"
        exit 0
    }

    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

if (Test-PortInUse -LocalPort $Port) {
    Write-Error "Porta $Port já está em uso. Execute scripts\stop_all.ps1 ou libere a porta manualmente."
}

$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "src.api:app", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrorLogFile `
    -WindowStyle Hidden `
    -PassThru

$Process.Id | Set-Content -Path $PidFile -Encoding ascii
Start-Sleep -Seconds 2

try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method Get -TimeoutSec 5
    Write-Host "Backend iniciado em http://127.0.0.1:$Port - status: $($Health.status) - PID: $($Process.Id)"
} catch {
    Write-Host "Backend iniciado, mas o health check ainda não respondeu. Verifique logs\backend.log e logs\backend.error.log. PID: $($Process.Id)"
}
