@echo off
REM Starts NEC Next with a console window so any error is visible.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -X faulthandler -m necnext %*
) else (
  python -X faulthandler -m necnext %*
)
echo.
echo Exit code %ERRORLEVEL%. Details are also in necnext_error.log
pause
