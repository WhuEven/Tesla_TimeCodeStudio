"""CLI 端到端冒烟测试：3 段特斯拉片段 → 截取 + 拼接 + 时间码 → 导出。"""

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tcs.assgen import OSSStyle
from tcs.exporter import ExportRunner, build_plan
from tcs.media import find_ffmpeg, probe
from tcs.model import Clip, ExportSettings, Project, human_size

SRC = Path("/Volumes/TESLADRIVE/TeslaCam/SavedClips/2026-10-29_19-13-46")
FILES = [
    "2026-10-29_19-03-40-front.mp4",
    "2026-10-29_19-04-41-front.mp4",
    "2026-10-29_19-05-41-front.mp4",
]

project = Project()
for f in FILES:
    c = Clip.from_path(SRC / f)
    c.info = probe(SRC / f)
    c.out_point = c.info.duration
    project.clips.append(c)

# 中间片段截取 10s ~ 30s
project.clips[1].in_point = 10.0
project.clips[1].out_point = 30.0

total = project.total_duration()
print(f"片段: {len(project.clips)}  总时长: {total:.2f}s")

settings = ExportSettings(width=1280, height=720, video_bitrate_kbps=2000)
style = OSSStyle(location="杭州 丹枫路")
est = settings.target_size_bytes(total)
print(f"预估体积: {human_size(est)}")

plan = build_plan(
    project, settings, style,
    work_dir=Path("/tmp/tcstest/work"), output=Path("/tmp/tcstest/out.mp4"),
    ffmpeg=find_ffmpeg(), fonts_dir=Path(__file__).resolve().parent / "assets" / "fonts",
)
print(plan.summary())
print("ASS 行数:", plan.ass_path.read_text().count("Dialogue:"))

t0 = time.time()
runner = ExportRunner(plan, on_progress=lambda f: print(f"\r进度 {f*100:5.1f}%", end="", flush=True),
                      on_log=lambda s: print("  " + s))
out = runner.run()
size = out.stat().st_size
print(f"\n完成 {out}  实际 {human_size(size)}  预估 {human_size(est)}  "
      f"偏差 {(size/est-1)*100:+.1f}%  耗时 {time.time()-t0:.0f}s")
