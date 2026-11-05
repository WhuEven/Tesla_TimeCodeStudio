"""文件名解析与时间轴排序。

这些规则决定 OSD 上的时间码是否正确，一旦回归会直接体现在成片里，
所以覆盖得细一些：TeslaCam 的真实命名、各种分隔符、只有日期、
伪造日期、视角别名、缺失时间的推算。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tcs.naming import (
    TESLA_VIEW_ORDER,
    VIEW_LABELS,
    fill_missing_times,
    parse_name,
    sort_for_timeline,
)


class TestParseName(unittest.TestCase):
    def test_tesla_name(self):
        ni = parse_name("2026-10-29_19-03-40-front.mp4")
        self.assertEqual(ni.start, datetime(2026, 10, 29, 19, 3, 40))
        self.assertEqual(ni.view, "front")
        self.assertEqual(ni.view_label, "前视")
        self.assertEqual(ni.source, "tesla")
        self.assertTrue(ni.ok)

    def test_all_tesla_views(self):
        for view in VIEW_LABELS:
            with self.subTest(view=view):
                ni = parse_name(f"/tmp/2026-10-29_19-03-40-{view}.mp4")
                self.assertEqual(ni.view, view)

    def test_long_view_marker_wins(self):
        # "left_repeater" 不能被更短的 "left" 抢先匹配
        ni = parse_name("2026-10-29_19-03-40-left_repeater.mp4")
        self.assertEqual(ni.view, "left_repeater")

    def test_view_aliases(self):
        self.assertEqual(parse_name("clip-2026-10-29_19-03-40-rear.mp4").view, "back")
        self.assertEqual(parse_name("2026-10-29_19-03-40_left.mp4").view, "left_repeater")
        self.assertEqual(parse_name("2026-10-29_19-03-40.right.mp4").view, "right_repeater")

    def test_separator_variants(self):
        cases = [
            "2026-10-29 19.03.40",
            "2026-10-29-19-03-40",
            "20261029_190340",
            "20261029190340",
            "rec_2026.10.29_19-03-40",
        ]
        for stem in cases:
            with self.subTest(stem=stem):
                ni = parse_name(stem + ".mp4")
                self.assertEqual(ni.start, datetime(2026, 10, 29, 19, 3, 40), stem)

    def test_date_only(self):
        ni = parse_name("20261029.mp4")
        self.assertEqual(ni.start, datetime(2026, 10, 29, 0, 0, 0))
        self.assertEqual(ni.source, "generic")

    def test_no_time(self):
        ni = parse_name("random_clip.mp4")
        self.assertIsNone(ni.start)
        self.assertEqual(ni.source, "none")
        self.assertFalse(ni.ok)

    def test_invalid_date(self):
        # 月份 19 非法，不能抛异常，应降级为「解析不到」
        ni = parse_name("2026-19-29_19-03-40-front.mp4")
        self.assertIsNone(ni.start)

    def test_view_label_fallback(self):
        ni = parse_name("2026-10-29_19-03-40-front.mp4")
        self.assertEqual(ni.view_label, "前视")


class _Item:
    """fill_missing_times / sort_for_timeline 需要的鸭子类型对象。"""

    def __init__(self, name: str, start=None, duration=60.0, view=None, out_point=0.0):
        self.path = Path(name)
        self.start_dt = start
        self.info = type("I", (), {"video_length": duration})()
        self.out_point = out_point
        self.view = view
        self.time_source = "文件名"

    @property
    def duration(self):
        return self.info.video_length


class TestFillMissingTimes(unittest.TestCase):
    def test_forward_fill_from_previous(self):
        t0 = datetime(2026, 10, 29, 19, 0, 0)
        items = [_Item("a.mp4", t0), _Item("b.mp4", None, duration=60.0)]
        fill_missing_times(items)
        self.assertEqual(items[1].start_dt, t0 + timedelta(seconds=60))
        self.assertEqual(items[1].time_source, "推算")

    def test_respects_out_point(self):
        t0 = datetime(2026, 10, 29, 19, 0, 0)
        items = [_Item("a.mp4", t0, duration=60.0, out_point=30.0), _Item("b.mp4", None)]
        fill_missing_times(items)
        self.assertEqual(items[1].start_dt, t0 + timedelta(seconds=30))

    def test_backward_fill_when_first_missing(self):
        t1 = datetime(2026, 10, 29, 19, 5, 0)
        items = [_Item("a.mp4", None, duration=60.0), _Item("b.mp4", t1)]
        fill_missing_times(items)
        self.assertEqual(items[0].start_dt, t1 - timedelta(seconds=60))

    def test_known_times_untouched(self):
        t0 = datetime(2026, 10, 29, 19, 0, 0)
        items = [_Item("a.mp4", t0), _Item("b.mp4", t0 + timedelta(minutes=1))]
        fill_missing_times(items)
        self.assertEqual(items[0].start_dt, t0)
        self.assertEqual(items[0].time_source, "文件名")


class TestSortForTimeline(unittest.TestCase):
    def test_sorted_by_time_then_view_order(self):
        t = datetime(2026, 10, 29, 19, 0, 0)
        late = _Item("late.mp4", t + timedelta(minutes=1), view="front")
        back = _Item("back.mp4", t, view="back")
        front = _Item("front.mp4", t, view="front")
        order = sort_for_timeline([late, back, front])
        self.assertEqual([c.path.name for c in order], ["front.mp4", "back.mp4", "late.mp4"])
        self.assertLess(TESLA_VIEW_ORDER.index("front"), TESLA_VIEW_ORDER.index("back"))

    def test_items_without_time_sort_first(self):
        t = datetime(2026, 10, 29, 19, 0, 0)
        items = [_Item("with.mp4", t), _Item("none.mp4", None)]
        self.assertEqual([c.path.name for c in sort_for_timeline(items)],
                         ["none.mp4", "with.mp4"])


if __name__ == "__main__":
    unittest.main()
