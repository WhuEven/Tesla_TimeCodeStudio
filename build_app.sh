#!/bin/bash
# TimeCode Studio —— 生成 macOS 应用程序包
#
# 双击 .command 文件必然由「终端」打开，无法避免黑窗口；
# 本脚本把程序封装成标准 .app，双击后直接出现图形界面，不弹终端。
#
# 用法：  ./build_app.sh
# 产物：  ./TimeCode Studio.app   （双击即可运行）
# 排错：  运行日志在项目根目录 launch.log
#
# 关于主可执行文件（两个关键点）：
#   1) 不能是 shell 脚本：纯脚本文本没有可识别架构，Apple Silicon 上会被
#      当成 Intel 应用并索要 Rosetta。
#   2) 不能简单 exec 外面的 python：那样进程的 NSBundle 会变成
#      Python.framework 里的 Python.app，菜单栏 app 名因此显示 "Python"。
# 于是用 clang 编一个宿主二进制，直接链接 Python 动态库并调用 Py_BytesMain，
# 它位于 Contents/MacOS 内，系统即把进程认定为本 app（菜单栏显示正确名称）。
# 缺少 clang / Python framework 时回退为脚本启动器（功能可用，次优）。

set -e
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"

APP="$ROOT/TimeCode Studio.app"
CONTENTS="$APP/Contents"

# ---------------- 探测解释器 ----------------
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "错误：未找到 python3"; exit 1; }

PY_BASE_PREFIX="$("$PY" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || true)"
PY_VERSION="$("$PY" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo 3.9)"
PY_SITE="$("$PY" -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || true)"
PY_DYLIB="$PY_BASE_PREFIX/Python3"
PY_HEADERS="$PY_BASE_PREFIX/Headers"

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$CONTENTS/Frameworks"

# ---------------- 宿主二进制（优先方案） ----------------
EXEC_NAME="launcher"
ARCHS="arm64"
if [ -f "$PY_DYLIB" ] && [ -d "$PY_HEADERS" ] && command -v clang >/dev/null 2>&1; then
    cp "$PY_DYLIB" "$CONTENTS/Frameworks/Python3"
    codesign -s - --force "$CONTENTS/Frameworks/Python3" >/dev/null 2>&1 || true

    STUB_SRC="$CONTENTS/MacOS/__host.c"
    cat > "$STUB_SRC" <<'C'
/* TimeCode Studio 宿主：把 Python 解释器嵌入 app 包。
   Py_BytesMain 直接接管 argv，进程的可执行文件就位于本 app 内，
   因此 NSBundle.mainBundle 指向本 app，菜单栏显示 CFBundleName 而非 "Python"。 */
#include <Python.h>
int main(int argc, char **argv) {
    return Py_BytesMain(argc, argv);
}
C

    ARCHS="$(lipo -archs "$PY_DYLIB" 2>/dev/null || echo arm64)"
    CLANG_ARGS=""
    for a in $ARCHS; do CLANG_ARGS="$CLANG_ARGS -arch $a"; done

    if clang -O2 $CLANG_ARGS -I"$PY_HEADERS" "$STUB_SRC" \
            "$CONTENTS/Frameworks/Python3" -o "$CONTENTS/MacOS/TimeCodeStudio" 2>/dev/null; then
        install_name_tool -change "@rpath/Python3.framework/Versions/${PY_VERSION%.*}/Python3" \
            "@executable_path/../Frameworks/Python3" \
            "$CONTENTS/MacOS/TimeCodeStudio" 2>/dev/null || true
        install_name_tool -id "@executable_path/../Frameworks/Python3" \
            "$CONTENTS/Frameworks/Python3" 2>/dev/null || true
        codesign -s - --force "$CONTENTS/Frameworks/Python3" >/dev/null 2>&1 || true
        codesign -s - --force "$CONTENTS/MacOS/TimeCodeStudio" >/dev/null 2>&1 || true
        EXEC_NAME="TimeCodeStudio"
    fi
    rm -f "$STUB_SRC"
fi

# ---------------- 启动器脚本 ----------------
# 若存在宿主二进制则用它跑 main.py，否则回退到外部 python。
# 弹窗文本一律 ASCII：osascript 读 UTF-8 中文在部分区域设置下会乱码。
{
    echo '#!/bin/bash'
    echo '# 由 build_app.sh 生成；.app 被移动后请重新运行 build_app.sh'
    echo "ROOT=\"$ROOT\""
    echo "PY_HOME=\"$PY_BASE_PREFIX\""
    echo "PY_SITE=\"$PY_SITE\""
    cat <<'SH'
# TimeCode Studio launcher
# No terminal window: all output goes to a log file; errors raise a dialog.

DIR="$ROOT"
[ -d "$DIR" ] || DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
if [ ! -f "$DIR/main.py" ]; then
    osascript -e "display alert \"TimeCode Studio\" message \"main.py not found under: $DIR\" buttons {\"OK\"} default button 1" >/dev/null 2>&1
    exit 1
fi
cd "$DIR" || exit 1

# 日志：优先项目目录，不可写则回退临时目录
LOG="$DIR/launch.log"
if ! : > "$LOG" 2>/dev/null; then
    LOG="${TMPDIR:-/tmp}/TimeCodeStudio-launch.log"
    : > "$LOG" 2>/dev/null || LOG=/dev/null
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] launch TimeCode Studio" >> "$LOG"

alert() {
    [ -n "$TCS_NO_ALERT" ] && return 0
    [ "$LOG" = "/dev/null" ] || open -e "$LOG" >/dev/null 2>&1
    osascript -e "display alert \"$1\" message \"$2\" buttons {\"OK\"} default button 1" >/dev/null 2>&1 || true
}

# 优先：app 内嵌的 Python 宿主（进程归属本 app，菜单栏名称正确）
HOST="$DIR/TimeCode Studio.app/Contents/MacOS/TimeCodeStudio"
if [ -x "$HOST" ] && [ -f "$HOST" ] && [ -n "$PY_HOME" ] && [ -d "$PY_SITE" ]; then
    export PYTHONHOME="$PY_HOME"
    export PYTHONPATH="$PY_SITE"
    echo "host: $HOST (PYTHONHOME=$PY_HOME)" >> "$LOG"
    if [ -n "$TCS_DRYRUN" ]; then
        echo "dry-run: would start $HOST $DIR/main.py" >> "$LOG"
        exit 0
    fi
    "$HOST" "$DIR/main.py" >>"$LOG" 2>&1
    code=$?
    if [ $code -ne 0 ]; then
        echo "exit code $code" >> "$LOG"
        alert "TimeCode Studio quit unexpectedly" "Exit code $code. See launch.log for details."
    fi
    exit 0
fi

# 回退：外部解释器（菜单栏会显示 Python）
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
    alert "Python not found" "Please install Python 3.9+ and run build_app.sh again."
    exit 1
fi
if ! "$PY" -c "import PySide6, fontTools, imageio_ffmpeg" >>"$LOG" 2>&1; then
    echo "installing dependencies..." >> "$LOG"
    if ! "$PY" -m pip install -r "$DIR/requirements.txt" >>"$LOG" 2>&1; then
        alert "Dependency install failed" "Run manually: $PY -m pip install -r requirements.txt"
        exit 1
    fi
fi
echo "fallback python: $PY" >> "$LOG"
if [ -n "$TCS_DRYRUN" ]; then
    echo "dry-run: would start $PY $DIR/main.py" >> "$LOG"
    exit 0
fi
"$PY" "$DIR/main.py" >>"$LOG" 2>&1
code=$?
if [ $code -ne 0 ]; then
    echo "exit code $code" >> "$LOG"
    alert "TimeCode Studio quit unexpectedly" "Exit code $code. See launch.log for details."
fi
SH
} > "$CONTENTS/MacOS/launcher"
chmod +x "$CONTENTS/MacOS/launcher"

# ---------------- Info.plist ----------------
{
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>TimeCode Studio</string>
    <key>CFBundleDisplayName</key>
    <string>TimeCode Studio</string>
    <key>CFBundleIdentifier</key>
    <string>com.whueven.timecodestudio</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>${EXEC_NAME}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.video</string>
    <key>LSArchitecturePriority</key>
    <array>
PLIST
    for a in $ARCHS; do echo "        <string>$a</string>"; done
    cat <<PLIST
    </array>
</dict>
</plist>
PLIST
} > "$CONTENTS/Info.plist"

# ---------------- 图标：png -> iconset -> icns ----------------
if [ -f "$ROOT/assets/AppIcon.png" ] && command -v iconutil >/dev/null 2>&1; then
    ICONSET="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET"
    # 每个基本尺寸都要有 1x 与 @2x 两档，缺任何一档 Finder 会显示空白图标
    for s in 16 32 128 256 512; do
        sips -z "$s" "$s" "$ROOT/assets/AppIcon.png" \
            --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
        d=$((s * 2))
        sips -z "$d" "$d" "$ROOT/assets/AppIcon.png" \
            --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/AppIcon.icns" >/dev/null 2>&1 || true
    rm -rf "$(dirname "$ICONSET")"
fi

# 去掉隔离属性，避免首次双击被 Gatekeeper 拦下
xattr -cr "$APP" 2>/dev/null || true

# 刷新 LaunchServices 缓存，避免沿用旧的架构/图标判定
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true

echo "已生成：$APP"
echo "主可执行文件：Contents/MacOS/$EXEC_NAME（架构：$(lipo -archs "$CONTENTS/MacOS/$EXEC_NAME" 2>/dev/null || echo 未知)）"
if [ "$EXEC_NAME" = "TimeCodeStudio" ]; then
    echo "内嵌 Python $PY_VERSION：$PY_DYLIB"
    echo "（进程归属本 app，菜单栏显示 TimeCode Studio，不再显示 Python）"
else
    echo "警告：缺少 clang 或 Python framework，回退为脚本启动器——"
    echo "      功能可用，但菜单栏可能仍显示 Python，且可能被要求安装 Rosetta。"
fi
echo "双击即可运行（不弹终端）。日志：$ROOT/launch.log"
