@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 chatseek_desktop.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo ChatSeek could not start. Install 64-bit Python 3.12 and check the error above.
pause
exit /b 1
