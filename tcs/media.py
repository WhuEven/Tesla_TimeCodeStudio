"""媒体探测与 ffmpeg 定位。

优先使用系统 ffprobe，缺失时退化为解析 ``ffmpeg -i`` 的输出 —— 这样程序
在只装了静态 ffmpeg 的环境（例如 pip 的 imageio-ffmpeg 包）里也能工作。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .model import MediaInfo

_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_RES_RE = re.compile(r"[,\s](\d{2,5})x(\d{2,5})[\s,\[]")
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUD_RE = re.compile(r"Stream #\d+:\d+.*: Audio:")
_SR_RE = re.compile(r"(\d{4,6})\s*Hz")
_CH_RE = re.compile(r"stereo|mono|5\.1|7\.1")


def find_ffmpeg() -> str | None:
    """定位 ffmpeg：环境变量 > PATH > pip 静态包 > 项目内置。"""
    import os

    env = os.environ.get("TCS_FFMPEG") or os.environ.get("FFMPEG_BINARY")
    if env and Path(env).exists():
        return env
    for name in ("ffmpeg",):
        p = shutil.which(name)
        if p:
            return p
    try:  # pip install imageio-ffmpeg
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    local = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg"
    if local.exists():
        return str(local)
    return None


def find_ffprobe() -> str | None:
    import os

    env = os.environ.get("TCS_FFPROBE")
    if env and Path(env).exists():
        return env
    p = shutil.which("ffprobe")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"):
        if Path(cand).exists():
            return cand
    local = Path(__file__).resolve().parent.parent / "bin" / "ffprobe"
    if local.exists():
        return str(local)
    return None


def _probe_ffprobe(ffprobe: str, path: Path) -> MediaInfo:
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    raw = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    data = json.loads(raw or "{}")
    info = MediaInfo()
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info.width:
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            info.video_codec = st.get("codec_name", "")
            for key in ("avg_frame_rate", "r_frame_rate"):
                val = st.get(key) or ""
                if "/" in val:
                    num, _, den = val.partition("/")
                    try:
                        if float(den):
                            info.fps = float(num) / float(den)
                            break
                    except ValueError:
                        pass
            if st.get("duration"):
                try:
                    info.duration = float(st["duration"])
                except ValueError:
                    pass
        elif st.get("codec_type") == "audio":
            info.has_audio = True
            info.sample_rate = int(st.get("sample_rate") or 48000)
            info.channels = int(st.get("channels") or 2)
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            info.duration = max(info.duration, float(fmt["duration"]))
        except ValueError:
            pass
    if fmt.get("bit_rate"):
        try:
            info.bit_rate = int(fmt["bit_rate"])
        except ValueError:
            pass
    tags = fmt.get("tags", {}) or {}
    loc = [tags.get(k) for k in ("title", "comment") if tags.get(k)]
    info.location = " ".join(loc)
    return info


def _probe_ffmpeg(ffmpeg: str, path: Path) -> MediaInfo:
    """没有 ffprobe 时解析 ``ffmpeg -i`` 的 stderr。"""
    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                          capture_output=True, text=True, timeout=60)
    text = proc.stderr
    info = MediaInfo()

    m = _DUR_RE.search(text)
    if m:
        h, mi, s = m.groups()
        info.duration = int(h) * 3600 + int(mi) * 60 + float(s)

    # 特斯拉把地点写在 comment（街道）与 title（城市）标签里
    loc = []
    for key in ("title", "comment"):
        mt = re.search(rf"^\s*{key}\s*:\s*(.+?)\s*$", text, re.M)
        if mt and mt.group(1):
            loc.append(mt.group(1))
    info.location = " ".join(loc)

    # 只看视频流那一行，避免 Audio 行里的数字干扰
    for line in text.splitlines():
        if "Stream #" in line and "Video:" in line:
            mr = _RES_RE.search(line)
            if mr:
                info.width, info.height = int(mr.group(1)), int(mr.group(2))
            mf = _FPS_RE.search(line)
            if mf:
                try:
                    info.fps = float(mf.group(1))
                except ValueError:
                    pass
            info.video_codec = line.split("Video:")[1].strip().split(" ")[0].split(",")[0]
        elif "Stream #" in line and "Audio:" in line:
            info.has_audio = True
            msr = _SR_RE.search(line)
            if msr:
                info.sample_rate = int(msr.group(1))
            mch = _CH_RE.search(line)
            if mch:
                info.channels = {"mono": 1, "stereo": 2}.get(mch.group(0), 6 if "5.1" in line else 2)
    if not info.fps:
        info.fps = 30.0
    return info


def probe(path: str | Path) -> MediaInfo:
    """探测媒体文件。失败时抛异常，由调用方决定是否跳过。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            return _probe_ffprobe(ffprobe, path)
        except Exception:
            pass
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg/ffprobe，请先安装 ffmpeg")
    return _probe_ffmpeg(ffmpeg, path)


def extract_frame(ffmpeg: str, path: Path, at: float, out_png: Path,
                  width: int = 480) -> bool:
    """抽一帧存 PNG，用于界面预览。"""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{max(0.0, at):.3f}", "-i", str(path),
           "-frames:v", "1", "-vf", f"scale={width}:-2", str(out_png)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90, check=True)
        return out_png.exists()
    except Exception:
        return False
