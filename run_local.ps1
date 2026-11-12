# PowerShell runner for Workplace Compliance Desk
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# Free port 8000 if occupied by older lab servers
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "[*] Freeing port 8000 from PID $($_.OwningProcess)..."
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}

$PythonCmd = "python"
if (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe") {
    $PythonCmd = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
}

$env:DISABLE_SQLALCHEMY_CEXT = "1"
& $PythonCmd scripts/run_services.py
