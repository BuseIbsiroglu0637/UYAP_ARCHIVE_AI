@echo off
setlocal
chcp 65001 >nul
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
echo ==========================================================
echo UYAP Archive AI - RC15.9.38 TAM KURULUM - Native 2.7.64
echo ==========================================================
echo Bu kurulum bilgisayarda Python veya PyInstaller gerektirmez.
echo Ilk kurulumda resmi Python.org gomulu runtime paketi indirilir,
echo SHA-256 ile dogrulanir ve yalniz UYAPArsiv altinda kullanilir.
echo.
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\portable_installer.ps1" -Mode Install
set "EC=%ERRORLEVEL%"
echo.
if "%EC%"=="0" (
  echo Kurulum tamamlandi. Chrome veya Edge'i tamamen kapatip yeniden acin.
) else (
  echo Kurulum basarisiz. native-install-diagnostic.json ve native-install.log olustu.
)
echo.
pause
exit /b %EC%
