@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run install_windows.bat first.
    pause
    exit /b 1
)
set GRADIO_ANALYTICS_ENABLED=False
.venv\Scripts\python.exe mako.py
pause
