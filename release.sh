#!/bin/bash
# 一键发版：构建自包含 .app → 打 zip → 打 tag →（装了 gh 并已登录时）创建 GitHub Release
#
# 用法：
#   ./release.sh                 版本号取 TCS_VERSION，缺省 1.0.0
#   TCS_VERSION=1.0.1 ./release.sh
#
# 没装 gh 时只构建 + 打 tag，并把 zip 路径和校验值打印出来，
# 到网页 Releases 里手动「Draft a new release」上传即可。

set -e
cd "$(dirname "$0")" || exit 1

VERSION="${TCS_VERSION:-1.0.0}"
TAG="v$VERSION"
ZIP="dist/TimeCodeStudio-$VERSION-macOS-arm64.zip"
NOTES="dist/RELEASE_NOTES-$VERSION.md"

TCS_VERSION="$VERSION" ./build_app.sh --dist
SHA="$(shasum -a 256 "$ZIP" | awk '{print $1}')"

mkdir -p dist
{
    echo "## TimeCode Studio $VERSION"
    echo
    echo "macOS 应用包（Apple Silicon / macOS 11+），已内嵌 Python、PySide6 与 ffmpeg，**无需安装环境**。"
    echo
    echo "> Intel Mac 暂无预编译包，请按 README 的「从源码运行」方式使用。"
    echo
    echo "### 安装"
    echo
    echo '1. 下载 `'"$(basename "$ZIP")"'`，双击解压'
    echo '2. 把 `Time Code Studio.app` 拖到「应用程序」'
    echo '3. 首次打开：右键点图标 → **打开** → 仍要打开（应用未做 Apple 公证，属正常提示）'
    echo
    echo "### 功能"
    echo
    echo "- 导入 TeslaCam / 监控视频（文件、文件夹或拖拽），从文件名自动识别时间码"
    echo "- 单时间轴截取：入点 / 出点 / 预览点同屏可见，预览点落在截取范围外会变橙色提示"
    echo "- 从预览点实时播放（静音预览），预览点随播放移动，到出点自动停"
    echo "- 任选片段拼接、调序、逐帧对齐，拼接点不丢帧"
    echo "- 内置 5×7 点阵监控字体，OSD 显示日期时间（可加地点副行、半透明底板）"
    echo "- 导出可选分辨率 / 编码器 / 码率，实时预估体积"
    echo
    echo "### 校验"
    echo
    echo '```'
    echo "$SHA  $(basename "$ZIP")"
    echo '```'
} > "$NOTES"

echo "分发包：$ZIP"
echo "校验值：$SHA"

# gh 可能被装在 ~/.local/bin（无 brew / 无管理员权限时的安装位置），补进查找范围
if ! command -v gh >/dev/null 2>&1 && [ -x "$HOME/.local/bin/gh" ]; then
    PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v gh >/dev/null 2>&1; then
    echo
    echo "未安装 gh，未自动创建 Release。手动步骤："
    echo "  1) 打开 https://github.com/WhuEven/Tesla_TimeCodeStudio/releases/new"
    echo "  2) Tag 选 $TAG（不存在就先推：git push origin $TAG）"
    echo "  3) 把 $ZIP 拖进附件区，正文粘贴 $NOTES 的内容"
    echo "  或安装 gh 后重跑本脚本：brew install gh && gh auth login
  （没有 brew 时：下载官方 zip，把 bin/gh 放到 ~/.local/bin，再 gh auth login --web）"
    exit 0
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "gh 未登录，跳过自动发布：gh auth login 后重跑。"
    exit 0
fi

git tag -a "$TAG" -m "TimeCode Studio $VERSION" 2>/dev/null || true
git push origin "$TAG" 2>/dev/null || true
gh release create "$TAG" "$ZIP" --title "TimeCode Studio $VERSION" --notes-file "$NOTES"
echo "Release 已创建：https://github.com/WhuEven/Tesla_TimeCodeStudio/releases/tag/$TAG"
