"""导出管线：一次 ffmpeg 编码完成 截取 → 统一规格 → 拼接 → 叠加时间码。

设计要点
--------
1. 单次编码：全部片段通过一个 ``filter_complex`` 串起来，只经历一次
   有损编码，画质优于"逐段转中间文件再 concat"的两遍方案。
2. 规格统一：``scale``/``pad``/``setsar``/``fps``/``format`` 把不同分辨率
   的输入（如特斯拉 front 2896×1876 与侧摄 1448×938）拉平，``concat``
   才不会失败。
3. 时间码：叠加一条覆盖全时间轴的 ASS 字幕，``fontsdir`` 加载内置
   点阵字体。
4. 路径安全：ASS 与字体目录使用相对路径，子进程的 cwd 指向临时工作
   目录，绕开 Windows 盘符冒号等转义地狱。
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .assgen import OSSStyle, build_ass
from .model import Clip, ExportSettings, Project, round_up_even
from .naming import fill_missing_times, sort_for_timeline

EVEN_TARGETS = [(3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (960, 540), (640, 360)]


def resolve_output_size(project: Project, settings: ExportSettings) -> tuple[int, int]:
    """确定输出分辨率（偶数尺寸，编码器要求）。"""
    if settings.width and settings.height:
        return round_up_even(settings.width), round_up_even(settings.height)
    w, h = project.output_resolution()
    return round_up_even(w), round_up_even(h)


def resolve_output_fps(project: Project, settings: ExportSettings) -> tuple[float, bool]:
    """返回 (目标 fps, 是否需要 fps 滤镜统一)。"""
    clips = project.active_clips()
    src = {round(c.info.fps, 3) for c in clips if c.info.fps > 0}
    if len(src) == 1:
        base = src.pop()
    else:
        base = min(src) if src else 30.0
    target = settings.fps if settings.fps else base
    need = len(src) > 1 or abs(target - base) > 0.001 or (settings.fps and abs(settings.fps - base) > 0.001)
    return target, need


@dataclass
class ExportPlan:
    """一次导出的完整计划，可直接转成 ffmpeg 命令。"""

    inputs: list[Clip]
    filter_script: Path
    ass_path: Path | None
    work_dir: Path
    output: Path
    width: int
    height: int
    fps: float
    duration: float
    cmd: list[str]

    def summary(self) -> str:
        parts = [
            f"片段 {len(self.inputs)} 段 / 总时长 {self.duration:.1f}s",
            f"输出 {self.width}x{self.height}@{self.fps:.2f}fps",
        ]
        return "，".join(parts)


def build_plan(project: Project, settings: ExportSettings, style: OSSStyle,
               work_dir: Path, output: Path, ffmpeg: str,
               fonts_dir: Path) -> ExportPlan:
    work_dir.mkdir(parents=True, exist_ok=True)
    clips = project.active_clips()
    if not clips:
        raise ValueError("没有可导出的片段")

    # 文件名没带时间的，用相邻片段推算补齐，保证时间轴连续
    order: list[Clip] = sort_for_timeline(list(clips))
    if any(c.start_dt is None for c in order):
        pool = [c for c in project.clips]
        fill_missing_times(pool)
        mapping = {id(c): c.start_dt for c in pool}
        for c in order:
            c.start_dt = c.start_dt or mapping.get(id(c)) or c.start_dt

    out_w, out_h = resolve_output_size(project, settings)
    fps, need_fps = resolve_output_fps(project, settings)

    same_size = all(c.info.width == out_w and c.info.height == out_h for c in order)
    target_hw = (out_w, out_h)

    any_audio = settings.include_audio and any(c.info.has_audio for c in order)
    segs = []
    lines: list[str] = []
    concat_in: list[str] = []

    for i, c in enumerate(order):
        vin = f"[v{i}]"
        sin = f"[{i}:v]"
        chain = [f"{sin}"]
        in_pt, out_pt = max(0.0, c.in_point), min(c.out_point, c.info.duration)
        trim = []
        if in_pt > 0.01:
            trim.append(f"start={in_pt:.3f}")
        if out_pt < c.info.duration - 0.01:
            trim.append(f"end={out_pt:.3f}")
        if trim:
            chain.append("trim=" + ":".join(trim))
        chain.append("setpts=PTS-STARTPTS")
        if c.info.width != out_w or c.info.height != out_h:
            chain.append(
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos"
            )
            chain.append(
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
        chain.append("setsar=1")
        if need_fps:
            chain.append(f"fps={fps:.6f}")
        chain.append("format=yuv420p")
        # 注意：某些 ffmpeg build 不接受 "[0:v]," 开头的写法，
        # 标签后必须直接连接第一个滤镜，不能用逗号过渡
        lines.append(chain[0] + ",".join(chain[1:]) + vin)

        if any_audio:
            ain = f"[a{i}]"
            if c.info.has_audio:
                ach = [f"[{i}:a]"]
                atrim = []
                if in_pt > 0.01:
                    atrim.append(f"start={in_pt:.3f}")
                if out_pt < c.info.duration - 0.01:
                    atrim.append(f"end={out_pt:.3f}")
                if atrim:
                    ach.append("atrim=" + ":".join(atrim))
                ach.append("asetpts=PTS-STARTPTS")
                ach.append("aresample=48000")
                ach.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
                lines.append(ach[0] + ",".join(ach[1:]) + ain)
            else:
                dur = out_pt - in_pt
                lines.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},"
                    "asetpts=PTS-STARTPTS,"
                    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo" + ain
                )
            concat_in += [vin, ain]
        else:
            concat_in.append(vin)

        segs.append((c, in_pt, out_pt))

    n = len(order)
    if any_audio:
        lines.append("".join(concat_in) + f"concat=n={n}:v=1:a=1[vc][ac]")
    else:
        lines.append("".join(concat_in) + f"concat=n={n}:v=1:a=0[vc]")

    ass_path = None
    vlabel = "[vc]"
    if style.enabled:
        from .model import RenderSegment

        offset = 0.0
        rsegs: list[RenderSegment] = []
        for c, in_pt, out_pt in segs:
            dur = out_pt - in_pt
            if c.start_dt is not None:
                rsegs.append(RenderSegment(offset, dur, in_pt, c.start_dt))
            offset += dur
        ass_path = work_dir / "timeline.ass"
        build_ass(rsegs, out_w, out_h, style, ass_path, fps)
        lines.append(f"{vlabel}ass=filename=timeline.ass:fontsdir={fonts_dir.name}[vout]")
        vlabel = "[vout]"

    script = work_dir / "filter.txt"
    script.write_text(";\n".join(lines) + "\n", encoding="utf-8")

    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
    for c in order:
        cmd += ["-i", str(c.path)]
    cmd += ["-filter_complex_script", str(script), "-map", vlabel]
    if any_audio:
        cmd += ["-map", "[ac]"]

    if settings.quality_mode == "crf":
        if settings.encoder == "libx264":
            cmd += ["-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf)]
        else:
            cmd += ["-c:v", settings.encoder, "-q:v", str(max(1, settings.crf - 15))]
    else:
        cmd += ["-c:v", settings.encoder]
        if settings.encoder == "libx264":
            cmd += ["-preset", settings.preset]
        cmd += [
            "-b:v", f"{settings.video_bitrate_kbps}k",
            "-maxrate", f"{int(settings.video_bitrate_kbps * 1.45)}k",
            "-bufsize", f"{settings.video_bitrate_kbps * 2}k",
        ]
    if any_audio:
        cmd += ["-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k"]
    else:
        cmd += ["-an"]

    cmd += [
        "-r", f"{fps:.6f}",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(output),
    ]

    duration = sum(out_pt - in_pt for _, in_pt, out_pt in segs)
    return ExportPlan(
        inputs=order, filter_script=script, ass_path=ass_path, work_dir=work_dir,
        output=output, width=out_w, height=out_h, fps=fps, duration=duration, cmd=cmd,
    )


class ExportRunner:
    """阻塞式执行导出（供线程调用），逐条上报进度。"""

    def __init__(self, plan: ExportPlan, on_progress=None, on_log=None):
        self.plan = plan
        self.on_progress = on_progress or (lambda frac: None)
        self.on_log = on_log or (lambda line: None)
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def run(self) -> Path:
        plan = self.plan
        self.on_log("$ " + " ".join(shlex.quote(x) for x in plan.cmd[:2]) + " …")
        self.proc = subprocess.Popen(
            plan.cmd, cwd=str(plan.work_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, errors="replace",
        )
        total_us = max(1e-6, plan.duration * 1e6)
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.strip()
            if line.startswith("out_time_us="):
                try:
                    us = float(line.split("=", 1)[1])
                    self.on_progress(min(0.999, max(0.0, us / total_us)))
                except ValueError:
                    pass
            elif line.startswith("progress=end"):
                self.on_progress(1.0)
            elif line:
                self.on_log(line)
        rc = self.proc.wait()
        if self.cancelled:
            raise InterruptedError("已取消")
        if rc != 0 or not plan.output.exists():
            raise RuntimeError(f"ffmpeg 退出码 {rc}，导出失败（详见日志）")
        self.on_progress(1.0)
        return plan.output
