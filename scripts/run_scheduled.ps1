[CmdletBinding()]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyAt = "21:05",

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$logsDirectory = Join-Path $projectRoot "logs"
$schedulerLog = Join-Path $logsDirectory "scheduler.log"
New-Item -ItemType Directory -Path $logsDirectory -Force | Out-Null

function Write-SchedulerLog {
    param([Parameter(Mandatory)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $schedulerLog -Encoding UTF8 -Value "$timestamp | $Message"
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonCommand = $venvPython
    $pythonPrefix = @()
} else {
    $pythonCommand = "py.exe"
    $pythonPrefix = @("-3.14")
}

if ($ValidateOnly) {
    & $pythonCommand @pythonPrefix "manage.py" "check" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "A configuracao Django do ChaRadarzin nao passou na validacao."
    }
    & $pythonCommand @pythonPrefix "manage.py" "schedule_daily_collection" "--help" | Out-Null
    & $pythonCommand @pythonPrefix "manage.py" "run_collection_worker" "--help" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Os comandos da fila nao passaram na validacao."
    }
    Write-Output "Scheduler e worker validados: $pythonCommand"
    exit 0
}

Write-SchedulerLog "SCHEDULER_START daily_at=$DailyAt"
try {
    $scheduleOutput = & $pythonCommand @pythonPrefix "manage.py" `
        "schedule_daily_collection" "--at" $DailyAt 2>&1
    $scheduleExitCode = $LASTEXITCODE
} catch {
    Write-SchedulerLog "SCHEDULER_FAILED launcher_error"
    exit 1
}
if ($scheduleExitCode -ne 0) {
    Write-SchedulerLog "SCHEDULER_FAILED exit_code=$scheduleExitCode"
    exit $scheduleExitCode
}
Write-SchedulerLog "SCHEDULER_SUCCESS"

Write-SchedulerLog "WORKER_START"
try {
    $workerOutput = & $pythonCommand @pythonPrefix "manage.py" `
        "run_collection_worker" "--once" 2>&1
    $workerExitCode = $LASTEXITCODE
} catch {
    Write-SchedulerLog "WORKER_FAILED launcher_error"
    exit 1
}

if ($workerExitCode -eq 0) {
    Write-SchedulerLog "WORKER_SUCCESS"
} else {
    Write-SchedulerLog "WORKER_FAILED exit_code=$workerExitCode"
}
exit $workerExitCode
