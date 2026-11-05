"""ASS 字幕生成：颜色换算、时间格式、整秒跳变与文本转义。

时间码是成片里唯一"内容"层面的东西，字幕行错位会直接表现为 OSD 跳动
不均匀，所以这里逐行核对时间戳。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tcs.assgen import OSSStyle, _ass_color, _escape, _ts, build_ass
from tcs.model import RenderSegment


def dialogue_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


def parse_ts(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


class TestTimeAndColor(unittest.TestCase):
    def test_ts_format(self):
        self.assertEqual(_ts(0), "0:00:00.00")
        self.assertEqual(_ts(65.25), "0:01:05.25")
        self.assertEqual(_ts(3723.5), "1:02:03.50")
        self.assertEqual(_ts(-1), "0:00:00.00")

    def test_color_rgb(self):
        self.assertEqual(_ass_color("#FFFFFF"), "&H00FFFFFF")
        self.assertEqual(_ass_color("000000"), "&H00000000")
        self.assertEqual(_ass_color("#FF0000"), "&H000000FF")

    def test_color_rgba_alpha_inverted(self):
        # ASS 的 alpha 与 CSS 相反：00 = 不透明，FF = 全透明
        self.assertEqual(_ass_color("#00000000"), "&HFF000000")
        self.assertEqual(_ass_color("#00000080"), "&H7F000000")

    def test_color_invalid(self):
        self.assertEqual(_ass_color("nope"), "&H00FFFFFF")
        self.assertEqual(_ass_color(""), "&H00FFFFFF")

    def test_escape_braces_and_backslash(self):
        self.assertEqual(_escape("a{b}\\c"), "a｛b｝＼c")


class TestBuildAss(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.start = datetime(2026, 10, 29, 19, 3, 40)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, segments, style=None, width=1280, height=720, fps=30.0):
        out = self.dir / "t.ass"
        build_ass(segments, width, height, style or OSSStyle(), out, fps)
        return out.read_text(encoding="utf-8")

    def test_header(self):
        text = self.build([RenderSegment(0.0, 2.0, 0.0, self.start)])
        self.assertIn("[Script Info]", text)
        self.assertIn("ScriptType: v4.00+", text)
        self.assertIn("PlayResX: 1280", text)
        self.assertIn("PlayResY: 720", text)
        self.assertIn("Style: OSD,TC OSD Dot", text)

    def test_one_line_per_second_boundary(self):
        # 3.5 秒 → 4 行：0-1, 1-2, 2-3, 3-3.5
        text = self.build([RenderSegment(0.0, 3.5, 0.0, self.start)])
        lines = dialogue_lines(text)
        self.assertEqual(len(lines), 4)
        ends = [parse_ts(ln.split(",")[2]) for ln in lines]
        self.assertEqual(ends, [1.0, 2.0, 3.0, 3.5])

    def test_timestamps_are_absolute_wall_clock(self):
        text = self.build([RenderSegment(0.0, 2.5, 0.0, self.start)])
        self.assertIn("2026-10-29 19:03:40", text)
        self.assertIn("2026-10-29 19:03:42", text)

    def test_segment_offset_and_src_in(self):
        # 第二段从输出第 5 秒开始，源内入点 10 秒 → 第一行应显示 19:03:50
        seg = RenderSegment(5.0, 1.0, 10.0, self.start)
        text = self.build([seg])
        lines = dialogue_lines(text)
        self.assertEqual(parse_ts(lines[0].split(",")[1]), 5.0)
        self.assertIn("2026-10-29 19:03:50", lines[0])

    def test_offset_does_not_drift_across_segments(self):
        # 两段首尾相接，时间戳应正好接上，不因累计误差跳秒
        segs = [RenderSegment(0.0, 1.0, 0.0, self.start),
                RenderSegment(1.0, 1.0, 0.0, self.start)]
        lines = dialogue_lines(self.build(segs))
        self.assertEqual([parse_ts(ln.split(",")[1]) for ln in lines], [0.0, 1.0])

    def test_location_second_line(self):
        style = OSSStyle(location="杭州 丹枫路")
        text = self.build([RenderSegment(0.0, 1.0, 0.0, self.start)], style)
        self.assertIn("杭州 丹枫路\\N2026-10-29 19:03:40", text)

    def test_location_is_escaped(self):
        style = OSSStyle(location="A{b}\\c")
        text = self.build([RenderSegment(0.0, 1.0, 0.0, self.start)], style)
        self.assertIn("A｛b｝＼c\\N", text)

    def test_sub_second_segment_still_emitted(self):
        text = self.build([RenderSegment(0.0, 0.2, 0.0, self.start)])
        self.assertEqual(len(dialogue_lines(text)), 1)

    def test_zero_length_segment_skipped(self):
        text = self.build([RenderSegment(0.0, 0.0, 0.0, self.start)])
        self.assertEqual(dialogue_lines(text), [])

    def test_custom_time_format(self):
        style = OSSStyle(time_format="%H:%M:%S")
        text = self.build([RenderSegment(0.0, 1.0, 0.0, self.start)], style)
        self.assertIn("19:03:40", text)
        self.assertNotIn("2026-10-29", text)

    def test_font_size_scales_with_height(self):
        text = self.build([RenderSegment(0.0, 1.0, 0.0, self.start)], height=1080)
        # 1080 × 0.040 ≈ 43
        self.assertRegex(text, r"Style: OSD,[^,]+,43,")

    def test_box_style_uses_border_style_3(self):
        text = self.build([RenderSegment(0.0, 1.0, 0.0, self.start)],
                          OSSStyle(box=True))
        style_line = next(ln for ln in text.splitlines() if ln.startswith("Style: OSD"))
        self.assertIn(",3,", style_line)

    def test_long_gap_stays_contiguous(self):
        # TeslaCam 段间约 1 秒缝隙：时间码要如实跳 1 秒，而不是被抹平
        segs = [RenderSegment(0.0, 60.0, 0.0, self.start),
                RenderSegment(60.0, 60.0, 0.0, self.start.replace(minute=4, second=41))]
        lines = dialogue_lines(self.build(segs))
        self.assertIn("2026-10-29 19:04:39", lines[59])   # 第一段末尾
        self.assertIn("2026-10-29 19:04:41", lines[60])   # 第二段开头（跳 1 秒）

    def test_no_dialogue_overlap(self):
        segs = [RenderSegment(0.0, 5.0, 0.0, self.start),
                RenderSegment(5.0, 5.0, 0.0, self.start)]
        lines = dialogue_lines(self.build(segs))
        starts = [parse_ts(ln.split(",")[1]) for ln in lines]
        ends = [parse_ts(ln.split(",")[2]) for ln in lines]
        for i in range(len(lines) - 1):
            self.assertAlmostEqual(ends[i], starts[i + 1], places=2)


if __name__ == "__main__":
    unittest.main()
