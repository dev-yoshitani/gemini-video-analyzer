@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto VENV
where py >nul 2>&1
if not errorlevel 1 goto PYLAUNCHER
goto SYSTEMPYTHON

:VENV
".venv\Scripts\python.exe" launcher.py --language en %*
exit /b %ERRORLEVEL%

:PYLAUNCHER
py -3 launcher.py --language en %*
exit /b %ERRORLEVEL%

:SYSTEMPYTHON
python launcher.py --language en %*
exit /b %ERRORLEVEL%
