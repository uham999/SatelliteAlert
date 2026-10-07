@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

if exist ".venv\Scripts\python.exe" goto dependencies
echo Creating a private Python environment for Satellite Alert...
if not "%~1"=="" goto explicit_python

rem Prefer Python 3.14; also accept an installed Python 3.12 or later.
py -3.14 -c "import sys; sys.exit(not ((3, 12) <= sys.version_info[:2] < (4, 0)))" >nul 2>&1
if errorlevel 1 goto default_launcher
py -3.14 -m venv .venv
goto environment_created

:default_launcher
py -3 -c "import sys; sys.exit(not ((3, 12) <= sys.version_info[:2] < (4, 0)))" >nul 2>&1
if errorlevel 1 goto path_python
py -3 -m venv .venv
goto environment_created

:path_python
python -c "import sys; sys.exit(not ((3, 12) <= sys.version_info[:2] < (4, 0)))" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv
goto environment_created

:explicit_python
"%~1" -c "import sys; sys.exit(not ((3, 12) <= sys.version_info[:2] < (4, 0)))" >nul 2>&1
if errorlevel 1 goto missing_python
"%~1" -m venv .venv

:environment_created
if errorlevel 1 goto environment_failed
if not exist ".venv\Scripts\python.exe" goto environment_failed

:dependencies
echo Using the app's private environment:
".venv\Scripts\python.exe" --version
".venv\Scripts\python.exe" -c "import sys; sys.exit(not ((3, 12) <= sys.version_info[:2] < (4, 0)))"
if errorlevel 1 goto missing_python
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto installation_failed
echo Installation complete. Double-click Start.cmd to run Satellite Alert.
pause
exit /b 0

:missing_python
echo Python 3.12 or later is required. Python 3.14 is recommended.
echo Install Python from https://www.python.org/downloads/
echo With the Python Install Manager, run: pymanager install 3.14
echo Then open a new terminal or double-click Install.cmd again.
pause
exit /b 1

:environment_failed
echo Could not create the private Python environment. See the messages above.
pause
exit /b 1

:installation_failed
echo Installation failed. Check your internet connection and the messages above.
pause
exit /b 1
