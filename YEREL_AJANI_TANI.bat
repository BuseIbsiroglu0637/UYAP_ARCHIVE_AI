@echo off
setlocal
chcp 65001 >nul
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\portable_installer.ps1" -Mode Diagnose
set "EC=%ERRORLEVEL%"
echo.
echo Tanilama dosyasi: %%LOCALAPPDATA%%\UYAPArsiv\native-install-diagnostic.json
echo Log dosyasi: %%LOCALAPPDATA%%\UYAPArsiv\native-install.log
pause
exit /b %EC%
