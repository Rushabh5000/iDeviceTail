# Start iDeviceTail (Windows). Usage: .\scripts\run.ps1 [-Port 3017] [-AgentPort 45455] [-NoStore]
param(
    [int]$Port = 3017,
    [int]$AgentPort = 45455,
    [switch]$NoStore,
    [string]$BindPolicy = "lan"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# set up the venv only if it isn't already usable
$venvOk = $false
if (Test-Path ".venv\Scripts\python.exe") {
    & ".\.venv\Scripts\python.exe" -c "import idevicetail, aiohttp, zeroconf, aiosqlite" 2>$null
    if ($LASTEXITCODE -eq 0) { $venvOk = $true }
}
if (-not $venvOk) { & "$PSScriptRoot\setup-desktop.ps1" }

$args = @("-m", "idevicetail", "serve", "--host", "0.0.0.0", "--port", "$Port", "--agent-port", "$AgentPort", "--bind-policy", $BindPolicy)
if ($NoStore) { $args += "--no-store" }

Write-Host "http://localhost:$Port"
& ".\.venv\Scripts\python.exe" @args
