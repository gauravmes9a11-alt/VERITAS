Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Generating Instant Public Website URL via LocalTunnel..." -ForegroundColor Cyan
Write-Host "Make sure VERITAS is running first (via run_local.bat or run_local.ps1)." -ForegroundColor Yellow
Write-Host "==================================================================" -ForegroundColor Cyan

npx localtunnel --port 8000
