"""数据模型：时长换算、片段派生属性、项目时间轴与体积预估。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tcs.model import (
    Clip,
    ExportSettings,
    MediaInfo,
    Project,
    fmt_hms,
    human_size,
    nice_bitrate_for,
    parse_hms,
    round_up_even,
)


def make_clip(name: str, start: datetime | None = None, seconds: float = 60.0,
              view: str = "front", size=(1920, 1080), fps: float = 30.0) -> Clip:
    c = Clip(path=Path(name), start_dt=start, view=view)
    c.info = MediaInfo(duration=seconds + 0.05, video_duration=seconds,
                       frame_count=int(seconds * fps), width=size[0], height=size[1],
                       fps=fps, has_audio=True)
    c.out_point = seconds
    return c


class TestTimeFormat(unittest.TestCase):
    def test_fmt_hms(self):
        self.assertEqual(fmt_hms(0), "0:00:00")
        self.assertEqual(fmt_hms(3661.4), "1:01:01")
        self.assertEqual(fmt_hms(65.2), "0:01:05")
        self.assertEqual(fmt_hms(-3), "0:00:00")

    def test_fmt_hms_ms(self):
        self.assertEqual(fmt_hms(65.25, True), "0:01:05.250")
        self.assertEqual(fmt_hms(3600, True), "1:00:00.000")

    def test_parse_hms(self):
        self.assertAlmostEqual(parse_hms("1:02:03.5"), 3723.5)
        self.assertAlmostEqual(parse_hms("02:03"), 123.0)
        self.assertAlmostEqual(parse_hms("42"), 42.0)
        self.assertAlmostEqual(parse_hms(" 1:00:00,250 "), 3600.25)
        self.assertIsNone(parse_hms(""))
        self.assertIsNone(parse_hms("abc"))
        self.assertIsNone(parse_hms("1:2:3:4"))

    def test_round_trip(self):
        for sec in (0.0, 6.5, 61.125, 3599.999):
            self.assertAlmostEqual(parse_hms(fmt_hms(sec, True)), sec, places=3)

    def test_human_size(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1024), "1.00 KB")
        self.assertEqual(human_size(1536 * 1024), "1.50 MB")
        self.assertEqual(human_size(2.5 * 1024 ** 3), "2.50 GB")


class TestMediaInfo(unittest.TestCase):
    def test_prefers_video_length(self):
        info = MediaInfo(duration=60.4, video_duration=60.0)
        self.assertEqual(info.video_length, 60.0)

    def test_falls_back_to_container_duration(self):
        self.assertEqual(MediaInfo(duration=12.5).video_length, 12.5)

    def test_aspect_default(self):
        self.assertAlmostEqual(MediaInfo().aspect, 16 / 9)


class TestClip(unittest.TestCase):
    def test_duration_uses_video_length(self):
        c = make_clip("a.mp4", seconds=60.0)
        self.assertEqual(c.duration, 60.0)
        self.assertEqual(c.clip_duration, 60.0)

    def test_clip_duration_with_trim(self):
        c = make_clip("a.mp4", seconds=60.0)
        c.in_point, c.out_point = 10.0, 30.0
        self.assertAlmostEqual(c.clip_duration, 20.0)

    def test_clip_duration_clamped_to_source(self):
        c = make_clip("a.mp4", seconds=60.0)
        c.in_point, c.out_point = -5.0, 999.0
        self.assertAlmostEqual(c.clip_duration, 60.0)

    def test_clip_duration_never_negative(self):
        c = make_clip("a.mp4", seconds=60.0)
        c.in_point, c.out_point = 30.0, 10.0
        self.assertEqual(c.clip_duration, 0.0)

    def test_absolute_times(self):
        t0 = datetime(2026, 10, 29, 19, 3, 40)
        c = make_clip("a.mp4", t0, seconds=60.0)
        c.in_point, c.out_point = 10.0, 30.0
        self.assertEqual(c.in_dt, t0 + timedelta(seconds=10))
        self.assertEqual(c.end_dt, t0 + timedelta(seconds=30))
        self.assertEqual(c.tc_span(), "19:03:50 → 19:04:10")

    def test_text_when_time_unknown(self):
        c = make_clip("a.mp4", None)
        self.assertEqual(c.start_text(), "—")
        self.assertEqual(c.tc_span(), "—")
        self.assertIsNone(c.in_dt)
        self.assertIsNone(c.end_dt)


class TestProject(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 29, 19, 0, 0)
        self.p = Project()
        self.front = make_clip("front.mp4", self.t0, 60.0, "front")
        self.back = make_clip("back.mp4", self.t0, 60.0, "back")
        self.late = make_clip("late.mp4", self.t0 + timedelta(minutes=1), 60.0, "front")
        self.p.clips = [self.front, self.back, self.late]

    def test_active_clips_respects_enabled(self):
        self.back.enabled = False
        self.assertEqual([c.path.name for c in self.p.active_clips()],
                         ["front.mp4", "late.mp4"])

    def test_view_filter_limits_scope(self):
        self.p.view_filter = "back"
        self.assertEqual([c.path.name for c in self.p.active_clips()], ["back.mp4"])
        self.assertEqual(self.p.count_selected(), (1, 1))

    def test_zero_length_clip_excluded(self):
        self.late.in_point = self.late.out_point = 5.0
        self.assertEqual(len(self.p.active_clips()), 2)

    def test_total_duration(self):
        self.front.in_point, self.front.out_point = 10.0, 30.0
        self.assertAlmostEqual(self.p.total_duration(), 2 * 60.0 + 20.0)

    def test_build_segments_offsets(self):
        self.front.in_point, self.front.out_point = 10.0, 30.0
        segs = self.p.build_segments()
        self.assertEqual(len(segs), 3)
        self.assertAlmostEqual(segs[0].offset, 0.0)
        self.assertAlmostEqual(segs[0].duration, 20.0)
        self.assertAlmostEqual(segs[0].src_in, 10.0)
        self.assertAlmostEqual(segs[1].offset, 20.0)
        self.assertAlmostEqual(segs[2].offset, 80.0)
        self.assertEqual(segs[2].start_dt, self.t0 + timedelta(minutes=1))

    def test_segments_skip_clips_without_time(self):
        self.late.start_dt = None
        self.assertEqual(len(self.p.build_segments()), 2)

    def test_selected_span(self):
        # 起点取所有启用片段的「入点」最早者（back 未截取，仍是整点开始）
        start, end = self.p.selected_span()
        self.assertEqual(start, self.t0)
        self.assertEqual(end, self.t0 + timedelta(minutes=2))

    def test_selected_span_after_trim(self):
        for c in self.p.clips:
            c.in_point = 10.0
        start, end = self.p.selected_span()
        self.assertEqual(start, self.t0 + timedelta(seconds=10))
        self.assertEqual(end, self.t0 + timedelta(minutes=2))

    def test_output_resolution_and_fps_follow_first_clip(self):
        self.back.info.width, self.back.info.height = 1280, 720
        self.assertEqual(self.p.output_resolution(), (1920, 1080))
        self.assertAlmostEqual(self.p.dominant_fps(), 30.0)

    def test_empties(self):
        p = Project()
        self.assertEqual(p.active_clips(), [])
        self.assertEqual(p.total_duration(), 0.0)
        self.assertEqual(p.selected_span(), (None, None))
        self.assertEqual(p.output_resolution(), (1920, 1080))
        self.assertAlmostEqual(p.dominant_fps(), 30.0)


class TestExportSettings(unittest.TestCase):
    def test_bitrate_estimate(self):
        s = ExportSettings(video_bitrate_kbps=8000, audio_bitrate_kbps=128)
        # 60s × (8000+128)kbps × 0.96 / 8
        expect = (8128 * 1000) * 60 / 8 * 0.96
        self.assertAlmostEqual(s.target_size_bytes(60), expect, delta=1)

    def test_audio_can_be_excluded(self):
        s = ExportSettings(video_bitrate_kbps=8000, include_audio=False)
        with_audio = ExportSettings(video_bitrate_kbps=8000).target_size_bytes(60)
        self.assertLess(s.target_size_bytes(60), with_audio)

    def test_zero_duration(self):
        self.assertEqual(ExportSettings().target_size_bytes(0), 0.0)

    def test_crf_estimate_shrinks_as_crf_grows(self):
        sizes = [ExportSettings(quality_mode="crf", crf=c, width=1920, height=1080)
                 .target_size_bytes(60) for c in (18, 23, 28)]
        self.assertGreater(sizes[0], sizes[1])
        self.assertGreater(sizes[1], sizes[2])


class TestHelpers(unittest.TestCase):
    def test_nice_bitrate_thresholds(self):
        self.assertEqual(nice_bitrate_for(3840, 2160), 40000)
        self.assertEqual(nice_bitrate_for(1920, 1080), 8000)
        self.assertEqual(nice_bitrate_for(1280, 720), 4000)
        self.assertEqual(nice_bitrate_for(640, 360), 2500)

    def test_round_up_even(self):
        self.assertEqual(round_up_even(721), 722)
        self.assertEqual(round_up_even(720), 720)


if __name__ == "__main__":
    unittest.main()
