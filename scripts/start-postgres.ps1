$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')
& docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
