"""数据模型：媒体探测结果、片段、项目与导出参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .naming import parse_name


def fmt_hms(seconds: float, ms: bool = False) -> str:
    """秒 -> ``h:mm:ss`` 或 ``h:mm:ss.mmm``。"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if ms:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{h}:{m:02d}:{int(s):02d}"


def parse_hms(text: str) -> float | None:
    """``h:mm:ss.mmm`` / ``mm:ss`` / ``ss`` -> 秒。"""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def human_size(num_bytes: float) -> str:
    if num_bytes < 1024:
        return f"{num_bytes:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        num_bytes /= 1024.0
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.2f} {unit}"
    return f"{num_bytes:.2f} TB"


@dataclass
class MediaInfo:
    """ffprobe / ffmpeg 探测出的流信息。"""

    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    sample_rate: int = 48000
    channels: int = 2
    video_codec: str = ""
    bit_rate: int = 0
    location: str = ""   # 特斯拉会写入 comment=街道 / title=城市

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


@dataclass
class Clip:
    """一个视频片段（对应磁盘上的一个文件）。"""

    path: Path
    info: MediaInfo = field(default_factory=MediaInfo)

    start_dt: datetime | None = None
    view: str | None = None
    time_source: str = "文件名"

    in_point: float = 0.0
    out_point: float = 0.0
    enabled: bool = True
    note: str = ""

    @classmethod
    def from_path(cls, path: str | Path) -> "Clip":
        path = Path(path)
        ni = parse_name(path)
        return cls(path=path, start_dt=ni.start, view=ni.view,
                   time_source="文件名" if ni.ok else "未识别")

    # ---- 派生属性 -------------------------------------------------
    @property
    def duration(self) -> float:
        return self.info.duration

    @property
    def clip_duration(self) -> float:
        """截取后的有效时长。"""
        d = self.info.duration
        if d <= 0:
            return 0.0
        return max(0.0, min(self.out_point, d) - max(0.0, self.in_point))

    @property
    def end_dt(self) -> datetime | None:
        if self.start_dt is None:
            return None
        return self.start_dt + timedelta(seconds=self.out_point or self.duration)

    @property
    def in_dt(self) -> datetime | None:
        if self.start_dt is None:
            return None
        return self.start_dt + timedelta(seconds=self.in_point)

    def start_text(self) -> str:
        if self.start_dt is None:
            return "—"
        return self.start_dt.strftime("%Y-%m-%d %H:%M:%S")

    def tc_span(self) -> str:
        if self.start_dt is None:
            return "—"
        a, b = self.in_dt, self.end_dt
        return f"{a:%H:%M:%S} → {b:%H:%M:%S}"


@dataclass
class ExportSettings:
    """导出参数。"""

    width: int = 0                 # 0 = 跟随首个启用片段
    height: int = 0
    fps: float = 0.0               # 0 = 跟随源
    video_bitrate_kbps: int = 8000
    audio_bitrate_kbps: int = 128
    encoder: str = "libx264"
    preset: str = "medium"
    quality_mode: str = "bitrate"  # bitrate | crf
    crf: int = 23
    include_audio: bool = True
    faststart: bool = True

    def target_size_bytes(self, duration: float) -> float:
        """按比特率估算体积。

        x264/videotoolbox 的 ABR 输出平均会略低于目标码率（实测约 -4%），
        容器开销 <1%，故系数取 0.96；实际大小通常落在预估 ±10% 内。
        """
        if self.quality_mode == "crf":
            # CRF 无法精确预测，用同类内容的经验 bpp 反推
            bpp = {18: 0.13, 20: 0.10, 23: 0.075, 26: 0.055, 28: 0.045}.get(
                min(self.crf, 40), 0.075
            )
            px = max(1, self.width) * max(1, self.height)
            fps = self.fps or 30
            v_bps = px * fps * bpp
        else:
            v_bps = self.video_bitrate_kbps * 1000
        a_bps = self.audio_bitrate_kbps * 1000 if self.include_audio else 0
        return (v_bps + a_bps) * max(0.0, duration) / 8.0 * 0.96


@dataclass
class RenderSegment:
    """输出时间轴上的一段，供时间码生成使用。"""

    offset: float        # 在输出文件中的起始秒
    duration: float
    src_in: float        # 源文件内的入点秒
    start_dt: datetime   # 源文件第 0 秒对应的绝对时间


class Project:
    """片段列表 + 全局时间轴计算。"""

    def __init__(self) -> None:
        self.clips: list[Clip] = []

    # ---- 列表维护 -------------------------------------------------
    def add_paths(self, paths, probe) -> tuple[int, int]:
        """导入文件，返回 (新增数, 跳过数)。probe 为探测函数。"""
        exist = {str(c.path) for c in self.clips}
        added = skipped = 0
        for p in paths:
            p = Path(p)
            if p.suffix.lower() not in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".ts", ".mpg", ".mpeg"}:
                skipped += 1
                continue
            if str(p) in exist:
                skipped += 1
                continue
            clip = Clip.from_path(p)
            try:
                clip.info = probe(p)
            except Exception:
                skipped += 1
                continue
            if clip.info.duration <= 0:
                skipped += 1
                continue
            clip.out_point = clip.info.duration
            self.clips.append(clip)
            exist.add(str(p))
            added += 1
        return added, skipped

    def active_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.enabled and c.clip_duration > 0.001]

    def total_duration(self) -> float:
        return sum(c.clip_duration for c in self.active_clips())

    def build_segments(self) -> list[RenderSegment]:
        """把启用片段串成输出时间轴，并给出各自的时间码锚点。"""
        segs: list[RenderSegment] = []
        offset = 0.0
        for c in self.active_clips():
            if c.start_dt is None:
                continue
            dur = c.clip_duration
            segs.append(RenderSegment(offset, dur, c.in_point, c.start_dt))
            offset += dur
        return segs

    def output_resolution(self) -> tuple[int, int]:
        clips = self.active_clips()
        if not clips:
            return 1920, 1080
        c = clips[0]
        return c.info.width, c.info.height

    def dominant_fps(self) -> float:
        clips = self.active_clips()
        if not clips:
            return 30.0
        return clips[0].info.fps or 30.0


def nice_bitrate_for(width: int, height: int) -> int:
    """按分辨率给一个合理的默认码率（kbps）。"""
    px = max(1, width * height)
    if px >= 3840 * 2160:
        return 40000
    if px >= 2560 * 1440:
        return 16000
    if px >= 1920 * 1080:
        return 8000
    if px >= 1280 * 960:
        return 6000
    if px >= 1280 * 720:
        return 4000
    return 2500


def round_up_even(n: int) -> int:
    return int(math.ceil(n / 2.0) * 2)
