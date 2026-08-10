param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $ProjectRoot "logs"

function Stop-ProcessTree {
    param([int]$ProcessId)

    $Children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($Child in $Children) {
        Stop-ProcessTree -ProcessId ([int]$Child.ProcessId)
    }

    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($Process) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-FromPidFile {
    param(
        [string]$Name,
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        Write-Host "$Name não possui PID registrado."
        return
    }

    $RawProcessId = Get-Content $PidFile -ErrorAction SilentlyContinue
    $ProcessId = 0
    if (-not [int]::TryParse(($RawProcessId | Select-Object -First 1), [ref]$ProcessId)) {
        Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "$Name sem PID válido."
        return
    }

    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($Process) {
        Stop-ProcessTree -ProcessId $ProcessId
        Write-Host "$Name encerrado. PID: $ProcessId"
    } else {
        Write-Host "$Name não estava em execução. PID antigo: $ProcessId"
    }

    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ListenersByPort {
    param(
        [string]$Name,
        [int]$Port
    )

    $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $Connections) {
        Write-Host "$Name sem processo ouvindo na porta $Port."
        return
    }

    $ProcessIds = $Connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ProcessId in $ProcessIds) {
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($Process) {
            Stop-ProcessTree -ProcessId ([int]$ProcessId)
            Write-Host "$Name encerrado pela porta $Port. PID: $ProcessId"
        }
    }
}

Stop-FromPidFile -Name "Backend" -PidFile (Join-Path $LogDir "backend.pid")
Stop-FromPidFile -Name "Frontend" -PidFile (Join-Path $LogDir "frontend.pid")
Stop-ListenersByPort -Name "Backend" -Port $BackendPort
Stop-ListenersByPort -Name "Frontend" -Port $FrontendPort
