[CmdletBinding()]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')] [string]$DailyAt = "03:15",
    [ValidateRange(1, 3650)] [int]$RetentionDays = 30,
    [string]$TaskName = "ChaRadarzin Backup",
    [string]$ExportDirectory = "",
    [switch]$Preview,
    [switch]$RunNow
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot "run_backup.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) { throw "Executor de backup ausente." }
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`" -RetentionDays $RetentionDays"
if ($ExportDirectory) { $arguments += " -ExportDirectory `"$ExportDirectory`"" }
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
if ($Preview) {
    [pscustomobject]@{ TaskName=$TaskName; DailyAt=$DailyAt; Arguments=$arguments; Register=$false }
    return
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Backup verificado e retencao do ChaRadarzin" -Force | Out-Null
if ($RunNow) { Start-ScheduledTask -TaskName $TaskName }
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime, NextRunTime, LastTaskResult
