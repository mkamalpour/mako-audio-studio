@echo off
setlocal
cd /d "%~dp0"
echo This check uses your active environment and installs nothing.
echo Activate an existing environment with requirements.txt dependencies.
python mako.py check
exit /b %errorlevel%
