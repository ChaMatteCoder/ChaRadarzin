[CmdletBinding()]
param(
    [switch]$ValidateOnly,

    [ValidateRange(0, 1440)]
    [int]$MinimumIntervalMinutes = 360
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$logsDirectory = Join-Path $projectRoot "logs"
$dataDirectory = Join-Path $projectRoot "data"
$schedulerLog = Join-Path $logsDirectory "scheduler.log"
$successMarker = Join-Path $dataDirectory "scheduler_last_success.txt"
New-Item -ItemType Directory -Path $logsDirectory, $dataDirectory -Force | Out-Null

function Write-SchedulerLog {
    param([Parameter(Mandatory)][string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $schedulerLog -Encoding UTF8 -Value "$timestamp | $Message"
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $pythonCommand = $venvPython
} else {
    $pythonCommand = "py.exe"
}

if ($ValidateOnly) {
    & $pythonCommand -m app.main --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "O executor Python do radar nao passou na validacao."
    }
    Write-Output "Executor agendado validado: $pythonCommand"
    exit 0
}

if (Test-Path -LiteralPath $successMarker) {
    $markerText = (Get-Content -LiteralPath $successMarker -Raw).Trim()
    $lastSuccess = [DateTimeOffset]::MinValue
    if (
        [DateTimeOffset]::TryParse(
            $markerText,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$lastSuccess
        )
    ) {
        $age = [DateTimeOffset]::Now - $lastSuccess
        if ($age.TotalMinutes -lt $MinimumIntervalMinutes) {
            Write-SchedulerLog "SKIPPED recent_success age_minutes=$([math]::Round($age.TotalMinutes, 1))"
            exit 0
        }
    }
}

Write-SchedulerLog "START python=$pythonCommand"
try {
    & $pythonCommand -m app.main --with-shipping --notify-summary
    $processExitCode = $LASTEXITCODE
} catch {
    Write-SchedulerLog "FAILED launcher_error"
    exit 1
}

if ($processExitCode -eq 0) {
    [DateTimeOffset]::Now.ToString("o") | Set-Content -LiteralPath $successMarker -Encoding UTF8
    Write-SchedulerLog "SUCCESS exit_code=0"
} else {
    Write-SchedulerLog "FAILED exit_code=$processExitCode"
}
exit $processExitCode
