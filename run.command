#!/bin/bash
# TimeCode Studio —— macOS 双击启动
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

# 1) 选择解释器：优先项目自带虚拟环境，避免依赖/污染系统 Python
if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "未找到 python3，请先安装：https://www.python.org 或 xcode-select --install"
    exit 1
fi

# 2) 依赖自检：缺失则自动安装到当前解释器环境
"$PY" -c "import PySide6, fontTools, imageio_ffmpeg" 2>/dev/null \
    || "$PY" -m pip install -r "$ROOT/requirements.txt" || exit 1

exec "$PY" "$ROOT/main.py"
