@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto VENV
where pyw >nul 2>&1
if not errorlevel 1 goto PYLAUNCHER
goto SYSTEMPYTHON

:VENV
start "" ".venv\Scripts\pythonw.exe" desktop_app.py %*
exit /b %ERRORLEVEL%

:PYLAUNCHER
start "" pyw -3 desktop_app.py %*
exit /b %ERRORLEVEL%

:SYSTEMPYTHON
start "" pythonw desktop_app.py %*
exit /b %ERRORLEVEL%
