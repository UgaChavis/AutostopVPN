@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Start-VPN.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
  echo Start-VPN failed with code %EXITCODE%.
)
pause
exit /b %EXITCODE%
