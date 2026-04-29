@echo off
cd /d C:\Users\chris\PROJECTS\agora
if not exist logs mkdir logs
if not exist data mkdir data
if "%AGORA_BIND_HOST%"=="" set AGORA_BIND_HOST=127.0.0.1
if "%AGORA_PORT%"=="" set AGORA_PORT=8890

for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%AGORA_PORT% ^| findstr LISTENING') do set PID=%%a
if defined PID (
    echo Agora gateway already running on port %AGORA_PORT% as process %PID%.
    exit /b 0
)

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set DT=%%I
set STAMP=%DT:~0,8%-%DT:~8,6%
echo http://%AGORA_BIND_HOST%:%AGORA_PORT%> data\gateway-url.txt
python -m agora.gateway >> logs\gateway-%STAMP%.log 2>&1
