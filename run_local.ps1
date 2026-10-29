# PowerShell runner for Workplace Compliance Desk
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
python scripts/run_services.py
