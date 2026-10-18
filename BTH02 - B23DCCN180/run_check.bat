@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Chua co moi truong ao. Hay chay setup.bat truoc.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python check_env.py
pause
