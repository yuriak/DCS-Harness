@echo off
setlocal

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 goto run_with_py

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 goto run_with_python

echo DCS-Harness setup requires Python 3.10 or newer. 1>&2
exit /b 1

:run_with_py
py -3 "%~dp0tools\src\py\setup.py" %*
exit /b %ERRORLEVEL%

:run_with_python
python "%~dp0tools\src\py\setup.py" %*
exit /b %ERRORLEVEL%
