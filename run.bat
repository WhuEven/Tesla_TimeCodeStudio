@echo off
rem TimeCode Studio - Windows 启动
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo 未找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause
    exit /b 1
)

python -c "import PySide6, fontTools, imageio_ffmpeg" 2>nul || python -m pip install -r requirements.txt

python main.py
pause
