@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set PYEXE=
for %%V in (3.12 3.11 3.10 3.9) do (
  if not defined PYEXE (
    py -%%V-64 -c "import sys" >nul 2>&1 && set PYEXE=py -%%V-64
  )
)
if not defined PYEXE (
  echo [LOI] Khong tim thay Python 3.9+ 64-bit. Cai Python 3.11 hoac 3.12 64-bit tu python.org
  echo Cac ban Python da cai:
  py -0p
  pause
  exit /b 1
)
echo Dung: %PYEXE%
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys,struct; sys.exit(0 if sys.version_info>=(3,9) and struct.calcsize('P')*8==64 else 1)" >nul 2>&1
  if errorlevel 1 (
    echo .venv cu dung Python cu, dang xoa de tao lai...
    rmdir /s /q .venv
  )
)
if not exist .venv\Scripts\python.exe %PYEXE% -m venv .venv
if not exist .venv\Scripts\activate.bat (
  echo [LOI] Khong tao duoc .venv
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python --version
python -m pip install -U pip
pip install -r requirements.txt
if errorlevel 1 (
  echo [LOI] Cai thu vien that bai. Chup man hinh loi gui lai.
  pause
  exit /b 1
)
if not exist .env copy .env.example .env
echo.
echo XONG. Mo file .env, dan token InfluxDB, roi chay run_check.bat
pause
