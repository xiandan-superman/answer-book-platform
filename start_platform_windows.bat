@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 scripts\source_launcher_gui.py
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw scripts\source_launcher_gui.py
  exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\source_launcher_gui.py
  exit /b %errorlevel%
)
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\source_launcher_gui.py
  exit /b %errorlevel%
)
echo Python 3.9 or newer is required.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 2
