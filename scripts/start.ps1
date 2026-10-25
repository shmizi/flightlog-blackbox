# Start the analysis API and n8n, each in its own window.
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# Close the two windows to stop them.

param(
    [string]$WatchFolder = (Join-Path (Split-Path $PSScriptRoot -Parent) "flight-logs")
)

$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No .venv found. Create it first: python -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}
New-Item -ItemType Directory -Force $WatchFolder | Out-Null

# API
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'FlightLog API'; & '$python' -m uvicorn api:app --host 127.0.0.1 --port 8000"
)

# n8n: enable the folder-watch node (disabled by default since n8n 2.0; command execution stays
# disabled) and let nodes read files only from the watched folder.
$n8nEnv = "`$env:NODES_EXCLUDE = '[\`"n8n-nodes-base.executeCommand\`"]'; " +
          "`$env:N8N_RESTRICT_FILE_ACCESS_TO = '$WatchFolder'; " +
          "`$env:N8N_DIAGNOSTICS_ENABLED = 'false'; "
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'FlightLog n8n'; $n8nEnv npx --yes n8n@2.43.7"
)

# Dashboard
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'FlightLog dashboard'; `$env:FLIGHTLOG_WATCH_FOLDER = '$WatchFolder'; " +
    "& '$python' -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false"
)

Write-Host "Started three windows: 'FlightLog API', 'FlightLog n8n' and 'FlightLog dashboard'."
Write-Host "When n8n prints 'Editor is now accessible', drop a log into: $WatchFolder"
Write-Host "Dashboard: http://localhost:8501   (opens in your browser)"
Write-Host "n8n editor (optional): http://localhost:5678"
