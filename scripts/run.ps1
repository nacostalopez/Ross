<#
Launches the Ross stack (Postgres/Timescale + FastAPI backend) via Docker
Compose. Plain deterministic wrapper, no AI involved.

Usage (from any directory):
    .\scripts\run.ps1            # build + run in the foreground, logs streaming
    .\scripts\run.ps1 -Detached  # build + run in the background
#>

param(
    [switch]$Detached
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    if ($Detached) {
        docker compose up --build -d
    } else {
        docker compose up --build
    }
} finally {
    Pop-Location
}
