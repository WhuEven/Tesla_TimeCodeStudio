#!/bin/bash
# TimeCode Studio —— macOS 双击启动
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3，请先安装：https://www.python.org 或 xcode-select --install"
    exit 1
fi

python3 -c "import PySide6, fontTools, imageio_ffmpeg" 2>/dev/null \
    || python3 -m pip install -r "$(pwd)/requirements.txt"

exec python3 "$(pwd)/main.py"
