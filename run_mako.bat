@echo off
setlocal
cd /d "%~dp0"
set GRADIO_ANALYTICS_ENABLED=False
python mako.py %*
