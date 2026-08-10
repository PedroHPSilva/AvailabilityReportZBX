param(
    [int]$Port = 3000
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FrontendDir = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $LogDir "frontend.pid"
$LogFile = Join-Path $LogDir "frontend.log"
$ErrorLogFile = Join-Path $LogDir "frontend.error.log"

function Test-PortInUse {
    param([int]$LocalPort)

    $Connection = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    return $null -ne $Connection
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Frontend já está em execução. PID: $ExistingPid"
        exit 0
    }

    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

if (Test-PortInUse -LocalPort $Port) {
    Write-Error "Porta $Port já está em uso. Execute scripts\stop_all.ps1 ou libere a porta manualmente."
}

$Npm = "npm"
$NpmCmd = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
if ($NpmCmd) {
    $Npm = $NpmCmd.Source
}

$Process = Start-Process `
    -FilePath $Npm `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$Port") `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrorLogFile `
    -WindowStyle Hidden `
    -PassThru

$Process.Id | Set-Content -Path $PidFile -Encoding ascii
Start-Sleep -Seconds 2

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host "Frontend iniciado em http://127.0.0.1:$Port - PID: $($Process.Id)"
} catch {
    Write-Host "Frontend iniciado, mas a página ainda não respondeu. Verifique logs\frontend.log e logs\frontend.error.log. PID: $($Process.Id)"
}
