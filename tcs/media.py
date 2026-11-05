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


_FRAME_COUNT_CACHE: dict[tuple[str, int, int], tuple[int, float]] = {}
# 最近一次统计的调试信息（命令、返回码与输出采样），测试失败时帮助定位
FRAME_COUNT_DEBUG = ""


def count_video_frames(ffmpeg: str, path: Path) -> tuple[int, float]:
    global FRAME_COUNT_DEBUG
    """统计视频流帧数与末帧结束时刻（只解复用、不解码，通常几十毫秒）。

    返回 ``(帧数, 时长)``。容器时长可能因音轨比视频长而偏大，
    按帧数算出的时长才是视频流真实长度。
    """
    path = Path(path)
    try:
        key = (str(path), path.stat().st_size, int(path.stat().st_mtime))
    except OSError:
        return 0, 0.0
    if key in _FRAME_COUNT_CACHE:
        return _FRAME_COUNT_CACHE[key]

    frames, last_time = 0, 0.0
    try:
        # 用 -progress 的 key=value 机器可读输出统计。注意 -progress 在
        # 6.x 里是「输出选项」，必须放在输出参数前面（7+ 才同时接受全局
        # 位置）；ffmpeg 9 起 stderr 经典 stats 行不可靠，且 `-c copy` 到
        # null 时其 time= 还会把封装器给视频流的起点偏移算进去。
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-nostats",
             "-i", str(path), "-map", "0:v:0", "-c", "copy",
             "-progress", "pipe:1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300, errors="replace",
        )
        text = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        FRAME_COUNT_DEBUG = (f"rc={proc.returncode} "
                             f"stdout[{len(proc.stdout or '')}]={text[:400]!r}")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("frame="):
                mf = re.search(r"frame=\s*(\d+)", line)
                if mf:
                    frames = max(frames, int(mf.group(1)))
            mu = re.search(r"^out_time_us=(\d+)", line)
            if mu:
                last_time = max(last_time, int(mu.group(1)) / 1e6)
                continue
            if "time=" in line and not line.startswith("out_time"):
                mt = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
                if mt:
                    h, mi, s = mt.groups()
                    last_time = max(last_time,
                                    int(h) * 3600 + int(mi) * 60 + float(s))
    except Exception as exc:
        frames, last_time = 0, 0.0
        FRAME_COUNT_DEBUG = f"demux raised: {exc!r}"

    if frames == 0:
        # 兜底：ffprobe 逐帧计数（要解码，慢一些但跨版本行为一致）。
        ffprobe = find_ffprobe()
        if ffprobe:
            try:
                raw = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "v:0",
                     "-count_frames", "-show_entries", "stream=nb_read_frames",
                     "-of", "default=nw=1:nk=1", str(path)],
                    capture_output=True, text=True, timeout=300,
                ).stdout.strip()
                if raw.isdigit():
                    frames = int(raw)
            except Exception:
                pass

    result = (frames, last_time)
    _FRAME_COUNT_CACHE[key] = result
    return result


def probe(path: str | Path) -> MediaInfo:
    """探测媒体文件。失败时抛异常，由调用方决定是否跳过。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    ffprobe = find_ffprobe()
    info = None
    if ffprobe:
        try:
            info = _probe_ffprobe(ffprobe, path)
        except Exception:
            info = None
    if info is None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("找不到 ffmpeg/ffprobe，请先安装 ffmpeg")
        info = _probe_ffmpeg(ffmpeg, path)

    _fill_video_length(info, path)
    return info


def _fill_video_length(info: MediaInfo, path: Path) -> None:
    """补上视频流的真实帧数与时长（拼接以视频为准）。"""
    if info.width <= 0:
        return
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return
    frames, last_time = count_video_frames(ffmpeg, path)
    info.frame_count = frames
    if frames > 0 and info.fps > 0:
        info.video_duration = frames / info.fps
    elif last_time > 0:
        info.video_duration = last_time


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
