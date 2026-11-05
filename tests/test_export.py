"""导出管线端到端：截取 → 统一规格 → 拼接 → 叠加 OSD。

重点回归「拼接点丢帧」：concat 以较长的那一路（通常是音轨）作为段尾，
音轨只要比视频长一点，视频末帧就会被反复复制。这里用音轨刻意加长的素材
检查输出帧数是否精确等于按帧边界吸附后的理论值。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tcs.assgen import OSSStyle
from tcs.exporter import ExportRunner, build_plan, resolve_output_fps, resolve_output_size
from tcs.media import find_ffmpeg, probe
from tcs.model import Clip, ExportSettings, Project
from tests.fixtures import make_clip

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def clip_from(path: Path, in_point: float = 0.0, out_point: float = 0.0) -> Clip:
    c = Clip.from_path(path)
    c.info = probe(path)
    c.out_point = out_point or c.info.video_length
    c.in_point = in_point
    return c


class TestPlan(unittest.TestCase):
    """不真正编码，只检查生成的 ffmpeg 计划。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.a = make_clip(cls.dir / "2026-10-29_19-00-00-front.mp4", 2.0, 24.0, (320, 180), 0.3)
        cls.b = make_clip(cls.dir / "2026-10-29_19-01-01-front.mp4", 2.0, 24.0, (240, 180), 0.3)
        cls.project = Project()
        cls.project.clips = [clip_from(cls.a), clip_from(cls.b)]
        cls.settings = ExportSettings(width=320, height=180, fps=24.0,
                                      video_bitrate_kbps=1200, preset="veryfast")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fresh_project(self) -> Project:
        p = Project()
        p.clips = [clip_from(self.a), clip_from(self.b)]
        return p

    def plan(self, project=None, style=None, keep_order=False, name="p1"):
        work = self.dir / ("work_" + name)
        return build_plan(project or self.project, self.settings,
                          style or OSSStyle(enabled=False), work,
                          work / "out.mp4", find_ffmpeg(), FONT_DIR,
                          keep_order=keep_order)

    def test_empty_project_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(Project(), name="empty")

    def test_even_output_size(self):
        s = ExportSettings(width=321, height=181)
        self.assertEqual(resolve_output_size(self.project, s), (322, 182))

    def test_output_size_follows_first_clip(self):
        # 合成素材是 320×180，未指定尺寸时跟随首个启用片段
        self.assertEqual(resolve_output_size(self.project, ExportSettings()), (320, 180))

    def test_fps_unified_when_requested(self):
        fps, need = resolve_output_fps(self.project, ExportSettings(fps=30.0))
        self.assertAlmostEqual(fps, 30.0)
        self.assertTrue(need)

    def test_filter_script_structure(self):
        p = self.plan()
        text = p.filter_script.read_text(encoding="utf-8")
        self.assertIn("concat=n=2:v=1:a=1", text)
        # trim 时间窗必须是帧边界
        self.assertIn("trim=start=0.000000:end=2.000000", text)
        # 不同分辨率要 scale + pad
        self.assertIn("scale=320:180", text)
        self.assertIn("pad=320:180", text)

    def test_frame_snapping(self):
        """非帧边界入出点应吸附到最近帧。"""
        project = self.fresh_project()
        single = Project()
        c = clip_from(self.a, in_point=0.51, out_point=1.49)
        single.clips = [c]
        plan = self.plan(project=single, name="snap")
        text = plan.filter_script.read_text(encoding="utf-8")
        self.assertIn("trim=start=0.500000:end=1.500000", text)
        # 0.5s→12 帧、1.5s→36 帧，共 24 帧
        self.assertAlmostEqual(plan.duration, 24 / 24.0, places=3)
        self.assertEqual(len(project.clips), 2)      # 未污染共享素材

    def test_sub_frame_window_keeps_one_frame(self):
        # 0.01s 的窗口吸附后会塌成 0 帧，必须兜底为 1 帧，否则 concat 段数不齐
        single = Project()
        single.clips = [clip_from(self.a, in_point=1.0, out_point=1.01)]
        plan = self.plan(project=single, name="tiny")
        self.assertAlmostEqual(plan.duration, 1 / 24.0, places=3)

    def test_zero_length_clip_not_exportable(self):
        single = Project()
        single.clips = [clip_from(self.a, in_point=1.0, out_point=1.0)]
        with self.assertRaises(ValueError):
            self.plan(project=single, name="zero")

    def test_ass_generated_with_osd(self):
        plan = self.plan(style=OSSStyle(location="杭州 丹枫路"), name="osd")
        self.assertIsNotNone(plan.ass_path)
        self.assertTrue(plan.ass_path.exists())
        text = plan.ass_path.read_text(encoding="utf-8")
        self.assertIn("2026-10-29 19:00:00", text)
        self.assertIn("杭州 丹枫路", text)
        script = plan.filter_script.read_text(encoding="utf-8")
        # 相对路径 + fontsdir，便于子进程 chdir 到工作目录
        self.assertIn("ass=filename=timeline.ass", script)

    def test_keep_order_overrides_timeline_sort(self):
        reversed_project = Project()
        reversed_project.clips = [clip_from(self.b), clip_from(self.a)]
        plan = self.plan(project=reversed_project, keep_order=True, name="order")
        self.assertEqual([c.path.name for c in plan.inputs],
                         [self.b.name, self.a.name])
        plan2 = self.plan(project=reversed_project, keep_order=False, name="order2")
        self.assertEqual([c.path.name for c in plan2.inputs],
                         [self.a.name, self.b.name])

    def test_audio_ends_before_video(self):
        script = self.plan().filter_script.read_text(encoding="utf-8")
        self.assertIn("atrim=start=0.000000:end=1.998000", script)  # out - 2ms


class TestRender(unittest.TestCase):
    """真实编码：帧数必须精确。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        # 音轨都比视频长 0.3s —— 触发历史上「拼接点重复末帧」的条件
        cls.a = make_clip(cls.dir / "2026-10-29_19-00-00-front.mp4", 3.0, 24.0, (320, 180), 0.3)
        cls.b = make_clip(cls.dir / "2026-10-29_19-01-01-front.mp4", 2.5, 24.0, (240, 180), 0.3)
        cls.settings = ExportSettings(width=320, height=180, fps=24.0,
                                      video_bitrate_kbps=1500, preset="veryfast")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def export(self, clips, name: str, style=None) -> Path:
        project = Project()
        project.clips = clips
        work = self.dir / ("out_" + name)
        plan = build_plan(project, self.settings, style or OSSStyle(enabled=False),
                          work, work / "out.mp4", find_ffmpeg(), FONT_DIR)
        return ExportRunner(plan, on_log=lambda _s: None).run()

    def test_full_clip_frame_count_exact(self):
        """整段导出：音轨多 0.3s 也不能让末帧被重复。"""
        out = self.export([clip_from(self.a)], "full")
        info = probe(out)
        self.assertEqual(info.frame_count, 72)       # 3.0s × 24fps
        self.assertAlmostEqual(info.video_duration, 3.0, places=2)

    def test_spliced_frame_count_is_sum_of_parts(self):
        a = clip_from(self.a)
        a.in_point, a.out_point = 0.5, 2.0          # 12 → 48 帧 = 36
        b = clip_from(self.b)
        b.in_point, b.out_point = 0.0, 2.0          # 0 → 48 帧 = 48
        out = self.export([a, b], "splice")
        info = probe(out)
        self.assertEqual(info.frame_count, 36 + 48)
        self.assertAlmostEqual(info.video_duration, 84 / 24.0, places=2)

    def test_output_has_audio(self):
        out = self.export([clip_from(self.a)], "audio")
        self.assertTrue(probe(out).has_audio)

    def test_unified_resolution(self):
        out = self.export([clip_from(self.a), clip_from(self.b)], "res")
        info = probe(out)
        self.assertEqual((info.width, info.height), (320, 180))

    def test_osd_burns_in(self):
        """叠加 OSD 后同一时刻的像素应发生变化。"""
        import subprocess

        plain = self.export([clip_from(self.a)], "plain")
        styled = self.export([clip_from(self.a)], "styled",
                             OSSStyle(location="杭州 丹枫路", font_size=28))

        def frame_bytes(path: Path, t: float) -> bytes:
            cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error",
                   "-ss", f"{t}", "-i", str(path), "-frames:v", "1",
                   "-f", "rawvideo", "-pix_fmt", "gray", "-"]
            return subprocess.run(cmd, capture_output=True, check=True).stdout

        self.assertNotEqual(frame_bytes(plain, 0.5), frame_bytes(styled, 0.5))

    def test_progress_reported(self):
        project = Project()
        project.clips = [clip_from(self.a)]
        work = self.dir / "out_progress"
        plan = build_plan(project, self.settings, OSSStyle(enabled=False),
                          work, work / "out.mp4", find_ffmpeg(), FONT_DIR)
        seen: list[float] = []
        ExportRunner(plan, on_progress=seen.append, on_log=lambda _s: None).run()
        self.assertTrue(seen)
        self.assertAlmostEqual(max(seen), 1.0)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in seen))


if __name__ == "__main__":
    unittest.main()
