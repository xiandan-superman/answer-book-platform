@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 scripts\source_launcher_gui.py --mode lan --autostart
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw scripts\source_launcher_gui.py --mode lan --autostart
  exit /b 0
)
call "%~dp0start_platform_windows.bat"
