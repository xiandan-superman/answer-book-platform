@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
if not exist "scripts\windows_launcher_bootstrap.py" (
  echo The program package is incomplete: scripts\windows_launcher_bootstrap.py is missing.
  echo Please extract the complete official source ZIP and try again.
  pause
  exit /b 3
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  py -3.11 -c "import sys" >nul 2>nul
  if !errorlevel! equ 0 (
    start "" pyw -3.11 scripts\windows_launcher_bootstrap.py %*
    exit /b 0
  )
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if !errorlevel! equ 0 (
    start "" pythonw scripts\windows_launcher_bootstrap.py %*
    exit /b 0
  )
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 scripts\windows_launcher_bootstrap.py %*
  set "launcher_rc=!errorlevel!"
  if not "!launcher_rc!"=="0" pause
  exit /b !launcher_rc!
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if !errorlevel! equ 0 (
    python scripts\windows_launcher_bootstrap.py %*
    set "launcher_rc=!errorlevel!"
    if not "!launcher_rc!"=="0" pause
    exit /b !launcher_rc!
  )
)
echo Python 3.11 or newer is required.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 2
