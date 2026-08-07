@echo off
echo Stopping backend on port 8002...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8002 ^| findstr LISTENING') do (
    echo Killing PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
ping 127.0.0.1 -n 3 >nul
echo Done. Now run start-backend.bat
