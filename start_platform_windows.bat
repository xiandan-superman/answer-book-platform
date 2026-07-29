@echo off
cd /d "%~dp0"
python scripts\start_platform.py --host 0.0.0.0 --port 8766
pause
