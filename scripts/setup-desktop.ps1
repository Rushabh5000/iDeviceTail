# iDeviceTail desktop setup (Windows / PowerShell)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3.10+ not found on PATH. Install from python.org and re-run."
}

# pymobiledevice3 (Engine A) needs C-extension deps that lack wheels for the
# newest CPython. Prefer 3.13 / 3.12 for the venv.
$pyExe = "python"
foreach ($v in "3.13", "3.12", "3.11") {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $pyExe = "py -$v"; break } } catch {}
}
Write-Host "Using interpreter: $pyExe"

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtualenv..."
    Invoke-Expression "$pyExe -m venv .venv"
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
try {
    & ".\.venv\Scripts\python.exe" -m pip install -e ".[device,dev]"
} catch {
    Write-Warning "Engine A (pymobiledevice3) failed to install - falling back to core only."
    Write-Warning "Use a Python 3.12/3.13 venv for system-log support. See docs/GUIDE.md."
    & ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"
}
& ".\.venv\Scripts\python.exe" -m idevicetail doctor

Write-Host ""
Write-Host "Add the firewall rule (once, elevated):" -ForegroundColor Cyan
Write-Host '  netsh advfirewall firewall add rule name="idevicetail" dir=in action=allow protocol=TCP localport=3017,45455 profile=private'
Write-Host ""
Write-Host "Then:  .\.venv\Scripts\python.exe -m idevicetail serve"
