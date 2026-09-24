@echo off
setlocal
chcp 65001 >nul
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
echo Bu islem SADECE yerel Native Agent'i ve Native Messaging kayitlarini kaldirir.
echo UYAP arsiv evraklari, final ZIP'ler ve kullanici verileri SILINMEZ.
echo.
set /p "OK=Devam etmek icin EVET yazin: "
if /I not "%OK%"=="EVET" exit /b 2
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\portable_installer.ps1" -Mode Uninstall
set "EC=%ERRORLEVEL%"
echo.
pause
exit /b %EC%
