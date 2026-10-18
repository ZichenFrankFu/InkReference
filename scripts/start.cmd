@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0open.ps1" %*
set "INKREFERENCE_EXIT_CODE=%ERRORLEVEL%"
if not "%INKREFERENCE_EXIT_CODE%"=="0" (
  echo.
  echo Startup failed. Review the error above.
  pause
)
exit /b %INKREFERENCE_EXIT_CODE%
