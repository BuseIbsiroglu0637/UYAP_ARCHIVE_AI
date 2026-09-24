@echo off
setlocal
chcp 65001 >nul
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
echo UYAP Archive AI RC15.9.37 - OFFLINE SETUP.EXE OLUSTURUCU
echo.
echo Bu adim GELISTIRICI bilgisayarinda bir kez calistirilir.
echo Resmi Python.org x64 runtime indirilir ve SHA-256 ile dogrulanir.
echo Ardindan Windows IExpress ile tek bir offline Setup.exe uretilir.
echo.
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\build_offline_setup.ps1" -Arch amd64
set "EC=%ERRORLEVEL%"
echo.
if "%EC%"=="0" echo Setup.exe olusturuldu. Bu EXE temiz bilgisayara gonderilebilir.
pause
exit /b %EC%
