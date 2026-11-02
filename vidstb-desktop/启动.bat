@echo off
cd /d "%~dp0"

where python >nul 2>nul && goto RUN_PYTHON
where py >nul 2>nul && goto RUN_PY

echo Python not found. Please install Python 3.9 or newer: https://www.python.org/downloads/
pause
exit /b 1

:RUN_PYTHON
python server.py
pause
exit /b 0

:RUN_PY
py server.py
pause
exit /b 0
