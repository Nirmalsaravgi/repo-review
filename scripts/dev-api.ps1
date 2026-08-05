# Activate venv first, then:
#   & d:/projects/gitHubRev/repo-review/.venv/Scripts/Activate.ps1
#   ./scripts/dev-api.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$env:PYTHONPATH = "packages/core/src;apps/api/src;apps/worker/src"
uvicorn api:app --reload --host 0.0.0.0 --port 8001 --app-dir apps/api/src
