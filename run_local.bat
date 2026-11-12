@echo off
setlocal
cd /d "%~dp0"

echo Checking and freeing port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Freeing port 8000 from PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)

set "PYTHON_EXE=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

set DISABLE_SQLALCHEMY_CEXT=1
"%PYTHON_EXE%" scripts\run_services.py
pause
