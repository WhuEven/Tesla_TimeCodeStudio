# TimeCode Studio

行车记录仪 / 监控视频的**时间码拼接工具**。针对 TeslaCam（特斯拉行车记录仪）
设计，同时兼容其他按时间命名的监控视频。

> **版权与授权**
> 本项目为**专有软件**，著作权归 **Haonan Zhu** 所有，代码公开仅供学习与非
> 商业使用。**任何商业性使用（含企业内部使用、对外付费服务、转售分发）均需
> 事先取得书面授权并支付授权费用**，详见 [`LICENSE`](LICENSE)。
>
> - 个人学习、研究、非商业使用：**免费，无需联系**
> - 商业授权：请在本仓库提 [Issue](https://github.com/WhuEven/Tesla_TimeCodeStudio/issues) 说明用途，作者将回复授权与报价

## 功能

- **导入**：选择文件 / 文件夹（如 `TeslaCam/SavedClips`），或直接拖拽；
  「清空列表」可一次移除全部片段（地点等自动填充项同步复位）；
  **打开目录会记住上次导入的位置**（清空、导出、重启程序后依然生效，
  目录已不存在时自动回退）
- **自动时间码**：从文件名解析拍摄起始时间（`2026-10-29_19-03-40-front.mp4`），
  按每个片段自己的时间戳生成**随时间跳动的 OSD**，格式 `年-月-日 时:分:秒`
- **监控专用字体**：内置 5×7 点阵字体（程序自动生成 TTF，无版权问题），
  另可选系统字体；支持描边 / 阴影 / 半透明底板 / 字号 / 副行文本
  （自动读取特斯拉文件里的地点元数据，如"杭州 丹枫路"；
  **重新导入以新素材为准**，手动改过这一栏则不再覆盖）
- **拼接与截取**：勾选「拼接」列即可任选其中几段参与导出（不必是连续片段，
  时间码在各段内仍精确锚定）；「显示视角」筛选导出范围；列表顺序即拼接顺序，
  可用 ↑/↓ 手动调整，或一键恢复按时间排序；每个片段可设入点/出点
  （双击列表输入，或用「截取片段」对话框：预览 / 播放 / 单时间轴上拖入出点）
- **帧精确拼接**：入出点自动吸附到帧边界，拼接点不丢帧、不重复帧
  （音频比视频提前 2ms 收尾，避免 concat 按较长的音轨复制视频末帧；
  截取窗口也按视频流真实帧数计算，不受容器时长里音轨余量影响）
- **导出**：单次编码（截取 → 统一分辨率 → 拼接 → 叠加时间码），
  可选分辨率 / 编码器（libx264 软编 / VideoToolbox 硬编）/ 码率，
  **实时预估导出体积**（±10% 内）；保存对话框默认停在上次导出的目录
- 导出过程有进度条与日志，完成后自动打开所在文件夹

## 下载（不想装 Python 的话）

到 [**Releases**](https://github.com/WhuEven/Tesla_TimeCodeStudio/releases/latest) 下载
`TimeCodeStudio-<版本>-macOS-arm64.zip`，解压后把 **`TimeCode Studio.app`**
拖进「应用程序」即可。包里已内嵌 Python、PySide6 与 ffmpeg，**不需要装任何环境**。

- 要求：macOS 11 以上、Apple Silicon（M 系列）。Intel Mac 请用下面的源码方式运行。
- 首次打开：应用是本地构建、未做 Apple 公证，macOS 会拦一次 ——
  **右键点图标 → 打开 → 仍要打开**（或「系统设置 → 隐私与安全性 → 仍要打开」）。
- 想校验下载：`shasum -a 256 TimeCodeStudio-*.zip`，与 Release 说明里的值比对。

## 运行（从源码）

```bash
# 依赖（推荐用项目内虚拟环境，避免污染系统 Python）
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 命令行启动
.venv/bin/python main.py
```

### macOS（推荐：双击图标，不弹终端）

```bash
./build_app.sh          # 生成 "TimeCode Studio.app"（含图标）
```

之后双击 **`TimeCode Studio.app`** 即可直接出现图形界面，**不会弹出终端窗口**。
可把它拖到 Dock 或「应用程序」文件夹；移动项目目录后重新执行一次 `./build_app.sh`
（脚本会把项目根目录写进启动器）。
启动日志写在项目根目录的 `launch.log`，异常退出时会弹窗提示。

这份开发态的 app 直接读仓库源码和 `.venv`（改代码即时生效），所以**只在构建机
上可用，不能拷给别人**。需要分发给别人（或上传 Release）时用：

```bash
./build_app.sh --dist   # 生成 dist/TimeCode Studio.app（自包含）+ 同名 zip
```

`--dist` 会把源码、Python 标准库、裁剪过的 PySide6、fontTools 与 ffmpeg 一并
装进 `Contents/Resources`，然后拷到临时目录做一次「脱仓库启动」验证，最后
打包成 `dist/TimeCodeStudio-<版本>-macOS-arm64.zip`（约 70 MB，解压约 190 MB）。
版本号可用 `TCS_VERSION=1.0.1 ./build_app.sh --dist` 覆盖。

一条命令完成发版（构建 → 打 tag → 创建 Release 并上传附件）：

```bash
TCS_VERSION=1.0.1 ./release.sh   # 需要 gh 且已 gh auth login
```

构建时会做两件关键的事：

- 用 `clang` 编译一个**原生宿主二进制**，直接链接 Python 动态库并调用
  `Py_BytesMain`（Python 与库文件一并放进 `Contents/`）。这样进程属于本 app，
  **菜单栏和 Dock 显示的是 "TimeCode Studio" 而不是 "Python"**；
  也不可能被误判成 Intel 应用去索要 Rosetta。
- 由 `assets/AppIcon.png` 生成完整的 `icns`（16–512 全部 1x/2x 尺寸，
  缺一档 Finder 就会显示空白图标）。

系统缺少 Command Line Tools（无 clang）或找不到 Python framework 时，
会自动回退成脚本启动器：功能照常，但菜单栏会显示 Python。

`run.command` 保留为**排错入口**（会用终端启动，方便看报错），
没有 `.app` 时会自动构建并打开它。它是给「拉了源码的人」用的，
不是给下载安装包的人用的。

### Windows

双击 `run.bat`：依赖就绪时用 `pythonw` 启动，**不显示黑色控制台窗口**。
（首次安装依赖阶段仍会有窗口显示进度。）

> ffmpeg 优先使用系统 PATH 里的（`brew install ffmpeg`）；没有时自动使用
> pip 附带的静态 ffmpeg，无需额外安装。

## 时间码原理（为什么精确）

TeslaCam 每段时长约 60 秒，但相邻段文件名间隔约 61 秒（有约 1 秒的录制
缝隙）。本工具**不用累计时长推算时间**，而是让每个片段直接使用自己文件名
里的时间戳：

```
显示时间 = 片段文件名时间戳 + 段内偏移（含截取入点）
```

因此缝隙处时间码会如实跳 1 秒，跨段、跨截取都不会漂移。
文件名被改过的片段会自动用相邻片段的时间推算补齐。

时间码以一条覆盖全时间轴的 ASS 字幕渲染（libass），`fontsdir` 加载内置
点阵字体，位置、字号、描边在任意分辨率下按画面高度等比缩放。

## 工程结构

```
main.py               入口
tcs/naming.py         文件名时间/视角解析（TeslaCam + 通用格式）
tcs/model.py          数据模型、体积预估
tcs/media.py          ffmpeg/ffprobe 定位与媒体探测
tcs/fontgen.py        内置点阵字体生成（fontTools）
tcs/assgen.py         OSD 时间码字幕（ASS）生成
tcs/exporter.py       导出管线（filter_complex 构建、进度）
tcs/timeline.py       截取用的单时间轴控件（入点 / 出点 / 预览点）
tcs/playback.py       预览播放器（ffmpeg 解码 → 逐帧画面）
tcs/ui_main.py        PySide6 界面
build_app.sh          生成 .app（--dist 生成自包含分发包）
release.sh            构建 + 打 tag +（可选）创建 GitHub Release
tests/smoke_cli.py    命令行端到端冒烟测试
```
