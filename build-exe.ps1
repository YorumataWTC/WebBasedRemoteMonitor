# Builds the standalone Windows executable: server\dist\RemoteMonitor\RemoteMonitor.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location "$root\web";    npm ci; npm run build; Pop-Location
Push-Location "$root\server"; uv sync; uv run remote-monitor models
uv run pyinstaller remote-monitor.spec --noconfirm
Pop-Location
Write-Host "Done: server\dist\RemoteMonitor\RemoteMonitor.exe"
