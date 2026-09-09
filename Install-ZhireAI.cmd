@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-ZhireAI.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Review the message above.
  pause
  exit /b 1
)
echo.
echo Installation completed.
pause
