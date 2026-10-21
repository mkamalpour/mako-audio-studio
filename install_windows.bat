@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe mako.py check
if errorlevel 1 goto failed
echo Setup complete. Run run_mako.bat to open Mako Audio Studio.
pause
exit /b 0
:failed
echo Setup failed. Read the error above before trying again.
pause
exit /b 1
