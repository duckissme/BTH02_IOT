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
python -m src.simulate_publisher --csv data/sim_raw.csv --minutes 120 && streamlit run app/app.py
pause
