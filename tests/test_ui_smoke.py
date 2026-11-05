"""界面冒烟：主窗口能建起来、导入能落到表格、截取对话框的入出点能往返。

用 offscreen 平台跑，不需要显示器；环境里没有 PySide6 或 Qt 平台插件
加载失败时整体跳过（核心逻辑另有 test_model / test_export 覆盖）。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.fixtures import make_clip


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _QtTest(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.app = _qapp()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Qt 不可用：{exc}")

    @classmethod
    def setUpClass_dir(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(tmp.name)
        cls._tmp = tmp
        return cls.dir


class TestMainWindow(_QtTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setUpClass_dir()
        try:
            from tcs.ui_main import MainWindow
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"界面模块导入失败：{exc}")
        cls.MainWindow = MainWindow

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def make_window(self):
        w = self.MainWindow()
        self.addCleanup(w.close)
        return w

    def test_window_builds(self):
        w = self.make_window()
        self.assertEqual(w.table.rowCount(), 0)
        self.assertTrue(w.windowTitle())

    def test_import_fills_table(self):
        path = make_clip(self.dir / "2026-10-29_19-03-40-front.mp4", 2.0, 24.0, (320, 180))
        w = self.make_window()
        w._import([str(path)])
        self.app.processEvents()
        self.assertEqual(len(w.project.clips), 1)
        self.assertEqual(w.table.rowCount(), 1)
        self.assertEqual(w.table.item(0, 2).text(), path.name)

    def test_import_deduplicates(self):
        path = make_clip(self.dir / "2026-10-29_19-04-40-front.mp4", 1.0, 24.0, (320, 180))
        w = self.make_window()
        w._import([str(path)])
        w._import([str(path)])
        self.assertEqual(len(w.project.clips), 1)

    def test_clear_empties_list(self):
        path = make_clip(self.dir / "2026-10-29_19-05-40-back.mp4", 1.0, 24.0, (320, 180))
        w = self.make_window()
        w._import([str(path)])
        self.assertTrue(w.project.clips)
        w.project.clips.clear()
        w.refresh_table()
        self.assertEqual(w.table.rowCount(), 0)

    def test_row_timecode_uses_file_name(self):
        path = make_clip(self.dir / "2026-10-29_19-06-40-front.mp4", 1.0, 24.0, (320, 180))
        w = self.make_window()
        w._import([str(path)])
        start = w.project.clips[0].start_dt
        self.assertEqual(start, datetime(2026, 10, 29, 19, 6, 40))


class TestTrimDialog(_QtTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setUpClass_dir()
        try:
            from tcs.ui_main import TrimDialog
            from tcs.media import find_ffmpeg, probe
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"界面模块导入失败：{exc}")
        cls.TrimDialog = TrimDialog
        cls.ffmpeg = find_ffmpeg()
        if not cls.ffmpeg:
            raise unittest.SkipTest("缺少 ffmpeg")
        cls.path = make_clip(cls.dir / "2026-10-29_19-07-40-front.mp4", 4.0, 24.0, (320, 180))
        cls.info = probe(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def make_clip_obj(self, in_point=1.0, out_point=3.0):
        from tcs.model import Clip

        c = Clip.from_path(self.path)
        c.info = self.info
        c.in_point, c.out_point = in_point, out_point
        return c

    def open_dialog(self, clip):
        d = self.TrimDialog(clip, self.ffmpeg)
        self.addCleanup(d.close)
        return d

    def test_cursor_starts_at_in_point(self):
        d = self.open_dialog(self.make_clip_obj(1.0, 3.0))
        self.assertAlmostEqual(d.bar.cursor(), 1.0, places=2)
        self.assertAlmostEqual(d.bar.in_point(), 1.0, places=3)
        self.assertAlmostEqual(d.bar.out_point(), 3.0, places=3)

    def test_full_range_when_untouched(self):
        c = self.make_clip_obj(0.0, 0.0)
        d = self.open_dialog(c)
        self.assertAlmostEqual(d.bar.in_point(), 0.0, places=3)
        self.assertAlmostEqual(d.bar.out_point(), d.bar.duration(), places=3)

    def test_apply_writes_back(self):
        c = self.make_clip_obj(1.0, 3.0)
        d = self.open_dialog(c)
        d.bar.set_range(0.5, 2.5)
        d.apply()
        self.assertAlmostEqual(c.in_point, 0.5, places=3)
        self.assertAlmostEqual(c.out_point, 2.5, places=3)

    def test_cursor_outside_range_detected(self):
        d = self.open_dialog(self.make_clip_obj(0.5, 1.5))
        d.bar.set_cursor(3.0)
        self.assertFalse(d.bar.cursor_inside())
        d.bar.set_cursor(1.0)
        self.assertTrue(d.bar.cursor_inside())

    def test_set_in_does_not_move_out(self):
        d = self.open_dialog(self.make_clip_obj(0.5, 1.5))
        before = d.bar.out_point()
        d.bar.set_cursor(1.0)
        d.bar.set_in_from_cursor()
        self.assertAlmostEqual(d.bar.in_point(), 1.0, places=2)
        self.assertAlmostEqual(d.bar.out_point(), before, places=3)

    def test_set_out_does_not_move_in(self):
        d = self.open_dialog(self.make_clip_obj(0.5, 1.5))
        before = d.bar.in_point()
        d.bar.set_cursor(3.0)
        d.bar.set_out_from_cursor()
        self.assertAlmostEqual(d.bar.out_point(), 3.0, places=2)
        self.assertAlmostEqual(d.bar.in_point(), before, places=3)

    def test_frame_step_matches_source_fps(self):
        d = self.open_dialog(self.make_clip_obj(0.0, 4.0))
        self.assertAlmostEqual(d.bar.frame_step(), 1 / 24.0, places=4)


if __name__ == "__main__":
    unittest.main()
