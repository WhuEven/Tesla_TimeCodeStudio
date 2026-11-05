"""媒体探测：ffmpeg/ffprobe 定位、容器与视频流时长、真实帧数统计。

这里的 ``frame_count`` / ``video_duration`` 是拼接不丢帧的基础 —— 音轨比
视频长时容器时长会偏大，必须按视频流真实帧数换算。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tcs.media import find_ffmpeg, find_ffprobe, probe
from tests.fixtures import make_clip


class TestBinaryDiscovery(unittest.TestCase):
    def test_ffmpeg_found(self):
        self.assertTrue(find_ffmpeg(), "CI 需要至少一个可用的 ffmpeg")

    def test_ffprobe_optional(self):
        # 允许为 None（回退到解析 ffmpeg -i 输出），有则必须是可执行文件
        p = find_ffprobe()
        if p:
            self.assertTrue(Path(p).exists())


class TestProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        # 音轨比视频长 0.3s：模拟真实录像里容器时长 > 视频流时长
        cls.path = make_clip(cls.dir / "2026-10-29_19-03-40-front.mp4",
                             seconds=3.0, fps=24.0, size=(320, 180), audio_pad=0.3)
        cls.info = probe(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dimensions_and_fps(self):
        self.assertEqual((self.info.width, self.info.height), (320, 180))
        self.assertAlmostEqual(self.info.fps, 24.0, places=2)

    def test_frame_count(self):
        self.assertEqual(self.info.frame_count, 72)   # 3.0s × 24fps

    def test_video_duration_from_frames(self):
        self.assertAlmostEqual(self.info.video_duration, 3.0, places=2)

    def test_container_duration_ge_video_duration(self):
        self.assertGreaterEqual(self.info.duration, self.info.video_duration - 1e-3)

    def test_video_length_prefers_frames(self):
        self.assertAlmostEqual(self.info.video_length, 3.0, places=2)

    def test_audio_detected(self):
        self.assertTrue(self.info.has_audio)
        self.assertEqual(self.info.sample_rate, 48000)
        self.assertGreaterEqual(self.info.channels, 1)

    def test_codec(self):
        self.assertEqual(self.info.video_codec, "h264")

    def test_video_only_clip(self):
        p = make_clip(self.dir / "mute.mp4", seconds=2.0, fps=30.0, with_audio=False)
        info = probe(p)
        self.assertFalse(info.has_audio)
        self.assertEqual(info.frame_count, 60)


if __name__ == "__main__":
    unittest.main()
