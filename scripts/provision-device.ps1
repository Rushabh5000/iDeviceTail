# One-time Engine-A provisioning for a USB-connected device (Windows).
# Usage: .\scripts\provision-device.ps1 [-Udid <UDID>]
param([string]$Udid)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"

if (-not $Udid) {
    Write-Host "Devices:"
    & $py -m idevicetail devices
    $Udid = Read-Host "Paste the UDID to provision"
}

Write-Host "`n1/3  Pairing (tap 'Trust' + passcode on the device) ..." -ForegroundColor Cyan
& $py -m idevicetail pair $Udid

Write-Host "`n2/3  Enabling Wi-Fi lockdown ..." -ForegroundColor Cyan
& $py -m idevicetail wifi-sync $Udid

Write-Host "`n3/3  Enabling Developer Mode (device will reboot) ..." -ForegroundColor Cyan
& $py -m idevicetail devmode $Udid

Write-Host "`nDone. After the device reboots, confirm Developer Mode under" -ForegroundColor Green
Write-Host "Settings > Privacy & Security > Developer Mode, then unplug the cable."
