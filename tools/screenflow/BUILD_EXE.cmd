@echo off
setlocal
cd /d "%~dp0"
uv sync --locked --no-dev --group build
if errorlevel 1 goto failed
uv run --locked --no-dev --group build pyinstaller --noconfirm --clean --windowed --onedir --name ScreenFlow --collect-all img2pdf --collect-all pyautogui launch.pyw
if errorlevel 1 goto failed
echo Build complete: dist\ScreenFlow\ScreenFlow.exe
pause
exit /b 0
:failed
echo Build failed. Review the message above.
pause
exit /b 1
