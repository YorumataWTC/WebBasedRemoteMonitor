# One-command start on Windows:  powershell -ExecutionPolicy Bypass -File start.ps1 [server options]
# Example: .\start.ps1 --source synthetic --password demo
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager)..."
    python -m pip install --user uv
    function uv { python -m uv @args }
}

Push-Location "$root\web"
if (-not (Test-Path "dist\index.html")) {
    Write-Host "Building web UI..."
    npm ci
    npm run build
}
Pop-Location

Push-Location "$root\server"
uv sync
uv run remote-monitor models
uv run remote-monitor serve @args
Pop-Location
