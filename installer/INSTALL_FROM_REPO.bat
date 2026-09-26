@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_FROM_REPO.ps1"
if errorlevel 1 (
  echo.
  echo [FAIL] Installation failed.
  pause
  exit /b 1
)
echo.
echo [PASS] Installation completed.
pause
