@echo off
echo Starting B2B Hunter AI Frontend...
cd /d "%~dp0frontend"
if not exist node_modules (
    echo Installing dependencies...
    call npm install
)
npm run dev
