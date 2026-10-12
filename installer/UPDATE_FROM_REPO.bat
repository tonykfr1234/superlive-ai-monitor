@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0UPDATE_FROM_REPO.ps1"
if errorlevel 1 (
  echo.
  echo [FAIL] Update failed. Review the message above before restarting.
  pause
  exit /b 1
)
echo.
echo [PASS] Update completed. Start the monitor when ready.
pause
