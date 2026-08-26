[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

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

& $pythonCommand -m app.main --with-shipping
exit $LASTEXITCODE
