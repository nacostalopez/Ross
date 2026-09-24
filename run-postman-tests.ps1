# Ross API - Postman Test Runner (Windows PowerShell)
# Usage: .\run-postman-tests.ps1 -Format "html" -Folder "AUTH"

param(
    [string]$Format = "cli",    # cli, html, json (default: cli)
    [string]$Folder = ""         # Optional: specific folder to run
)

# Configuration
$Collection = ".\Ross-API-Tests.postman_collection.json"
$Environment = ".\Ross-Env-Local.postman_environment.json"
$ResultsDir = ".\test-results"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

# Create results directory
if (-not (Test-Path $ResultsDir)) {
    New-Item -ItemType Directory -Path $ResultsDir | Out-Null
}

Write-Host "================================" -ForegroundColor Yellow
Write-Host "Ross API - Postman Test Suite" -ForegroundColor Yellow
Write-Host "================================" -ForegroundColor Yellow
Write-Host ""

# Check if files exist
if (-not (Test-Path $Collection)) {
    Write-Host "ERROR: Collection file not found: $Collection" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Environment)) {
    Write-Host "ERROR: Environment file not found: $Environment" -ForegroundColor Red
    exit 1
}

# Build newman command
$NewmanCmd = @("newman", "run", $Collection, "-e", $Environment)

if ($Folder) {
    Write-Host "Running folder: $Folder" -ForegroundColor Yellow
    $NewmanCmd += @("--folder", $Folder)
}

# Add reporters based on format
$ReportFile = ""
switch ($Format) {
    "html" {
        Write-Host "Format: HTML Report" -ForegroundColor Yellow
        $ReportFile = "$ResultsDir/report_$Timestamp.html"
        $NewmanCmd += @("--reporters", "cli,html", "--reporter-html-export", $ReportFile)
    }
    "json" {
        Write-Host "Format: JSON Report" -ForegroundColor Yellow
        $ReportFile = "$ResultsDir/report_$Timestamp.json"
        $NewmanCmd += @("--reporters", "cli,json", "--reporter-json-export", $ReportFile)
    }
    default {
        Write-Host "Format: CLI Output" -ForegroundColor Yellow
        $NewmanCmd += @("--reporters", "cli")
    }
}

# Get API URL from environment file
$EnvContent = Get-Content $Environment -Raw | ConvertFrom-Json
$ApiUrl = $EnvContent.values | Where-Object { $_.key -eq "base_url" } | Select-Object -ExpandProperty value
Write-Host "API URL: $ApiUrl" -ForegroundColor Yellow
Write-Host ""

# Run tests
Write-Host "Starting tests..." -ForegroundColor Yellow
Write-Host ""

& @NewmanCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✓ All tests passed!" -ForegroundColor Green
    
    if ($ReportFile -and (Test-Path $ReportFile)) {
        Write-Host "Report saved: $ReportFile" -ForegroundColor Green
    }
    
    exit 0
} else {
    Write-Host ""
    Write-Host "✗ Some tests failed" -ForegroundColor Red
    exit 1
}
