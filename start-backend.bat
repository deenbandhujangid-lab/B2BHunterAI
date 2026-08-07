@echo off
echo Starting B2B Hunter AI Backend (Windows-safe, no --reload)...
cd /d "%~dp0backend"
if not exist venv (
    echo Creating virtual environment with Python 3.11...
    py -3.11 -m venv venv
    call venv\Scripts\activate
    pip install -r requirements.txt
    playwright install chromium
) else (
    call venv\Scripts\activate
)
python run.py
