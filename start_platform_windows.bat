@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\source_launcher.py
  goto done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\source_launcher.py
  goto done
)
echo Python 3.9 or newer is required.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 2
:done
if not %errorlevel%==0 pause
