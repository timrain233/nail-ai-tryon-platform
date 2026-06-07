@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   NAIL AI - Deploy to Cloud Server
echo ========================================
echo.
echo Deploying to 101.200.233.235 ...
echo.

PowerShell -ExecutionPolicy Bypass -File "%~dp0deploy.ps1"

echo.
if %errorlevel% equ 0 (
    echo Done! Open http://101.200.233.235:7860/ on your phone.
) else (
    echo Failed - check the PowerShell output above.
)
echo.
pause
