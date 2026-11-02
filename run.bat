@echo off
rem TimeCode Studio - Windows 启动
cd /d "%~dp0"

rem 优先使用项目自带虚拟环境
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

where %PY% >nul 2>nul
if errorlevel 1 (
    echo 未找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH
    pause
    exit /b 1
)

%PY% -c "import PySide6, fontTools, imageio_ffmpeg" 2>nul || %PY% -m pip install -r requirements.txt

%PY% main.py
pause
