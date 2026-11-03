#!/bin/bash
# TimeCode Studio —— macOS 兼容入口
#
# 推荐直接双击「TimeCode Studio.app」（不弹终端，见 build_app.sh）。
# 本脚本保留给需要用终端排错的场景；若 .app 尚未生成，会自动构建后启动。

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
APP="$ROOT/TimeCode Studio.app"

if [ ! -d "$APP" ]; then
    echo "未找到 TimeCode Studio.app，正在生成…"
    ./build_app.sh || exit 1
fi

open -a "$APP"
echo "已启动 TimeCode Studio（以后可直接双击 TimeCode Studio.app）"
