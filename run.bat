@echo off
rem TimeCode Studio - Windows 启动（从源码运行，无控制台窗口）
rem 只想用软件、没有源码？Windows 版暂无打包，需先装 Python 3.9+ 再运行本脚本。
cd /d "%~dp0"

rem 优先使用项目自带虚拟环境
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

where %PY% >nul 2>nul
if errorlevel 1 (
    echo 未找到 python，请先安装 Python 3.9+ 并勾选 Add to PATH
    pause
    exit /b 1
)

rem 依赖自检（这一阶段仍需要窗口显示安装进度）
%PY% -c "import PySide6, fontTools, imageio_ffmpeg" 2>nul || %PY% -m pip install -r requirements.txt

rem 用 pythonw 启动图形界面，不弹出黑色控制台窗口
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
) else (
    start "" %PY% main.py
)
