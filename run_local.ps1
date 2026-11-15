# PowerShell runner for Workplace Compliance Desk
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PythonCmd = "python"
if (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe") {
    $PythonCmd = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
}

& $PythonCmd scripts/run_services.py
