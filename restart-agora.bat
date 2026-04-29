@echo off
cd /d C:\Users\chris\PROJECTS\agora
if "%AGORA_PORT%"=="" set AGORA_PORT=8890

:: Find what's on the configured Agora port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%AGORA_PORT% ^| findstr LISTENING') do set PID=%%a

:: Kill it if found
if defined PID (
    echo Stopping process %PID% on port %AGORA_PORT%...
    taskkill /F /PID %PID% >nul 2>&1
    timeout /t 2 >nul
)

:: Start fresh
echo Starting Agora gateway...
start "" "C:\Users\chris\PROJECTS\agora\start-agora.bat"
