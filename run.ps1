# Starts both services for a local demo. Each one needs its own window:
# the Payment Mock holds the request open on purpose when mode=timeout.
#
#   .\run.ps1          start both
#   .\run.ps1 -Reset   delete both databases first, then start

param([switch]$Reset)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Missing virtual environment. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

if ($Reset) {
    Remove-Item (Join-Path $root "order_api\orders.db*") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $root "payment_mock\payments.db*") -Force -ErrorAction SilentlyContinue
    Write-Host "Databases removed."
}

Start-Process -FilePath $python -ArgumentList "-m", "payment_mock.app" -WorkingDirectory $root
Start-Process -FilePath $python -ArgumentList "-m", "order_api.app" -WorkingDirectory $root

Write-Host "Payment Mock -> http://127.0.0.1:5001"
Write-Host "Order API    -> http://127.0.0.1:5000"
Write-Host "Stop them with Ctrl+C in each window."
