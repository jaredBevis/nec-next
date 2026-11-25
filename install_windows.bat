@echo off
REM Creates a private Python environment for NEC Next. Needs Python 3.9+ from python.org
cd /d "%~dp0"
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
echo.
echo Done. Double-click run_necnext.bat to start.
pause
