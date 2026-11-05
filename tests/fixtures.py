"""测试素材：用 ffmpeg 的 lavfi 源现场合成短片，避免依赖真实录像。

合成参数刻意贴近真实场景：
* 画面带运动（testsrc2 有移动方块与秒表），便于目视核对抽帧位置；
* 音轨默认比视频长 ``audio_pad`` 秒 —— 这正是容器时长大于视频流时长、
  曾经导致拼接点重复末帧的成因，回归测试要用到。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tcs.media import find_ffmpeg


def make_clip(
    path: str | Path,
    seconds: float = 3.0,
    fps: float = 24.0,
    size: tuple[int, int] = (320, 180),
    audio_pad: float = 0.0,
    with_audio: bool = True,
) -> Path:
    """合成一段测试视频，返回路径。

    ``audio_pad`` 让音轨比视频长若干秒，模拟真实录像里音轨多出的尾巴。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg，无法生成测试素材")

    w, h = size
    cmd = [
        ff, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds:.3f}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={seconds + audio_pad:.3f}"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", str(int(fps * 2)),
    ]
    if with_audio:
        cmd += ["-c:a", "aac", "-b:a", "64k"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path
