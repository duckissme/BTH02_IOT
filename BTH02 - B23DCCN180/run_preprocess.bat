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
python -m src.preprocess --source influx --start -2h --write-influx --report data/report.json
pause
