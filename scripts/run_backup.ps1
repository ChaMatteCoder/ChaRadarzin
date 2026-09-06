[CmdletBinding()]
param(
    [ValidateRange(1, 3650)] [int]$RetentionDays = 30,
    [string]$ExportDirectory = "",
    [switch]$ValidateOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($ValidateOnly) {
    docker compose --env-file .env.compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "A configuracao Compose nao e valida." }
    docker compose --env-file .env.compose exec -T web python manage.py backup_database --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "O comando de backup nao esta disponivel." }
    Write-Output "Rotina de backup validada."
    exit 0
}
docker compose --env-file .env.compose exec -T web python manage.py backup_database
if ($LASTEXITCODE -ne 0) { throw "O backup PostgreSQL falhou." }
docker compose --env-file .env.compose exec -T web python manage.py prune_backups --retention-days $RetentionDays --confirm
if ($LASTEXITCODE -ne 0) { throw "A retencao de backups falhou." }
docker compose --env-file .env.compose exec -T web python manage.py purge_expired_data --confirm
if ($LASTEXITCODE -ne 0) { throw "A retencao de dados tecnicos falhou." }
if ($ExportDirectory) {
    $destination = [IO.Path]::GetFullPath($ExportDirectory)
    if (-not (Test-Path -LiteralPath $destination -PathType Container)) { throw "O diretorio externo informado nao existe." }
    $localBackupRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "backups"))
    $latest = Get-ChildItem -LiteralPath $localBackupRoot -Filter "chadaradzin-*.dump" -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($null -eq $latest) { throw "Nenhum backup local foi encontrado para exportacao." }
    $manifest = "$($latest.FullName).json"
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "O manifesto do backup mais recente nao foi encontrado." }
    Copy-Item -LiteralPath $latest.FullName -Destination $destination -Force
    Copy-Item -LiteralPath $manifest -Destination $destination -Force
}
Write-Output "Backup, retencao e verificacao concluidos."
