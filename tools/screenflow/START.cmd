@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install uv and reopen this file.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\pythonw.exe" goto setup
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, PIL, img2pdf, pyautogui" >nul 2>nul
if errorlevel 1 goto setup
goto launch
:setup
echo Preparing ScreenFlow. The first launch requires an internet connection.
uv sync --locked --no-dev
if errorlevel 1 (
  echo Setup failed. Keep this message for troubleshooting.
  pause
  exit /b 1
)
:launch
start "" ".venv\Scripts\pythonw.exe" "%~dp0launch.pyw"
