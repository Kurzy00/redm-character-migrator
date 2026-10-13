@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [ERROR] Python not found in PATH. Install Python 3.10+ from https://www.python.org/downloads/
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating .venv...
  %PYTHON_CMD% -m venv ".venv"
  if errorlevel 1 (
    echo [ERROR] Could not create .venv.
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --quiet -e .
if errorlevel 1 (
  echo [ERROR] Could not install dependencies.
  exit /b 1
)

".venv\Scripts\python.exe" migrate.py %*
set "EXIT_CODE=%errorlevel%"

endlocal & exit /b %EXIT_CODE%
