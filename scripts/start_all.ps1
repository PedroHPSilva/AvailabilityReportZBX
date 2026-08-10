param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

& (Join-Path $PSScriptRoot "start_backend.ps1") -Port $BackendPort
& (Join-Path $PSScriptRoot "start_frontend.ps1") -Port $FrontendPort

Write-Host ""
Write-Host "Aplicação disponível em http://127.0.0.1:$FrontendPort"
Write-Host "Backend disponível em http://127.0.0.1:$BackendPort"
Write-Host "Logs: logs\backend.log, logs\backend.error.log, logs\frontend.log e logs\frontend.error.log"
