# Start the Champ Phone Validator web app and open it in the browser.
#   powershell -ExecutionPolicy Bypass -File .\run_app.ps1            (default port 8000)
#   powershell -ExecutionPolicy Bypass -File .\run_app.ps1 -Port 9000
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "First run: creating .venv and installing requirements..."
    py -3.12 -m venv .venv
    .\.venv\Scripts\python -m pip install --upgrade pip
    .\.venv\Scripts\python -m pip install -r requirements.txt
}

$url = "http://127.0.0.1:$Port"
Write-Host "Champ Phone Validator running at $url  (Ctrl+C to stop)"
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 2; Start-Process $u } -ArgumentList $url | Out-Null
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
