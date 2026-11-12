@echo off
echo ==================================================================
echo Generating Instant Public Website URL via LocalTunnel...
echo Make sure VERITAS is running first (via run_local.bat).
echo ==================================================================
npx localtunnel --port 8000
pause
