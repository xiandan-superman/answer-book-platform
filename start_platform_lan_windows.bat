@echo off
cd /d "%~dp0"
if not exist "scripts\windows_launcher_bootstrap.py" (
  call "%~dp0start_platform_windows.bat"
  exit /b %errorlevel%
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 scripts\windows_launcher_bootstrap.py --mode lan --autostart
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw scripts\windows_launcher_bootstrap.py --mode lan --autostart
  exit /b 0
)
call "%~dp0start_platform_windows.bat"
