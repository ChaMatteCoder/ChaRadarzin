[CmdletBinding()]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyAt = "09:00",

    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "Radar de Precos",

    [switch]$Preview,
    [switch]$RunNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot "run_scheduled.ps1"
if (-not (Test-Path -LiteralPath $runnerPath)) {
    throw "Executor agendado nao encontrado: $runnerPath"
}

$windowsPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $windowsPowerShell)) {
    throw "Windows PowerShell nao encontrado: $windowsPowerShell"
}

$triggerTime = [DateTime]::Today.Add(
    [TimeSpan]::ParseExact($DailyAt, "hh\:mm", [Globalization.CultureInfo]::InvariantCulture)
)
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`""

$action = New-ScheduledTaskAction `
    -Execute $windowsPowerShell `
    -Argument $actionArguments `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

if ($Preview) {
    [pscustomobject]@{
        TaskName = $TaskName
        DailyAt = $DailyAt
        Execute = $windowsPowerShell
        Arguments = $actionArguments
        WorkingDirectory = $projectRoot
        StartWhenAvailable = $true
        MultipleInstances = "IgnoreNew"
        RunOnlyIfNetworkAvailable = $true
        Register = $false
    }
    return
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Coleta diaria do Radar de Precos com frete" `
    -Force | Out-Null

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime, NextRunTime, LastTaskResult
