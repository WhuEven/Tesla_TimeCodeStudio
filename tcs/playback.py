"""预览播放器：用已依赖的 ffmpeg 解码出 RGB 帧，逐帧喂给界面。

为什么不用 QtMultimedia：PySide6 的 Essentials 包不含 QtMultimedia 框架，
装 Addons 会多出数百 MB 依赖，且打包进 .app 后框架加载路径更易出问题。
这里直接复用导出链路已经在用的 ffmpeg：

    ffmpeg -ss <起点> -i <文件> -an -vf fps=<fps>,scale=w:h -f rawvideo -pix_fmt rgb24 -

按 ``w*h*3`` 字节切帧，按帧率节流后通过信号送到主线程显示。预览播放为静音，
只用于核对截取点位，不影响导出（导出仍走原始文件 + 音频）。

线程安全：每次 ``play()`` 递增 generation，旧线程的残留信号带旧 generation，
界面据此丢弃，避免拖动时间轴时上一段播放把画面"抢"回去。
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage


class FramePlayer(QObject):
    """把 ffmpeg 输出的一帧帧画面交给界面。"""

    frameReady = Signal(int, object)        # (generation, QImage)
    positionChanged = Signal(int, float)    # (generation, 秒)
    finished = Signal(int)                  # 自然播完（到出点 / 文件结束）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._stop_evt: threading.Event | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    # ---------------- 对外接口 ----------------
    def generation(self) -> int:
        return self._gen

    def invalidate(self) -> int:
        """让当前播放线程的后续信号立即失效（不等待线程退出）。

        拖动时间轴时用：旧位置还在解码，若继续接受它的位置信号，
        预览点会被拉回旧位置，导致"拖完又跳回去"。
        """
        self._gen += 1
        return self._gen

    def play(self, ffmpeg: str, path: Path, start: float, fps: float,
             stop_at: float, width: int, height: int) -> int:
        """从 ``start`` 秒开始播放，到 ``stop_at`` 秒停。返回本次 generation。"""
        self.stop()
        self._gen += 1
        gen = self._gen
        evt = threading.Event()
        self._stop_evt = evt
        self._thread = threading.Thread(
            target=self._run,
            args=(gen, evt, ffmpeg, Path(path), float(start), float(fps),
                  float(stop_at), int(width), int(height)),
            daemon=True,
        )
        self._thread.start()
        return gen

    def stop(self):
        evt = self._stop_evt
        if evt is not None:
            evt.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=0.6)
        self._proc = None
        self._thread = None
        self._stop_evt = None

    # ---------------- 工作线程 ----------------
    @staticmethod
    def _read_exact(stream, size: int) -> bytes:
        chunks, got = [], 0
        while got < size:
            buf = stream.read(size - got)
            if not buf:
                break
            chunks.append(buf)
            got += len(buf)
        return b"".join(chunks) if chunks else b""

    def _run(self, gen: int, evt: threading.Event, ffmpeg: str, path: Path,
             start: float, fps: float, stop_at: float, w: int, h: int):
        fps = fps if fps > 0 else 30.0
        frame_bytes = w * h * 3
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{max(0.0, start):.3f}", "-i", str(path),
            "-an",
            "-vf", f"fps={fps:.6f},scale={w}:{h}:flags=bilinear",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=max(frame_bytes * 2, 1 << 20),
            )
            if gen == self._gen:
                self._proc = proc
            t0 = time.monotonic()
            i = 0
            while not evt.is_set():
                raw = self._read_exact(proc.stdout, frame_bytes)
                if len(raw) < frame_bytes:
                    break
                t = start + i / fps
                img = QImage(raw, w, h, w * 3, QImage.Format_RGB888).copy()
                if gen != self._gen:
                    break
                self.frameReady.emit(gen, img)
                self.positionChanged.emit(gen, t)
                i += 1
                if t >= stop_at - 1e-6:
                    break
                # 按帧率节流（分段睡，便于及时响应停止）
                target = t0 + i / fps
                while not evt.is_set():
                    delay = target - time.monotonic()
                    if delay <= 0:
                        break
                    time.sleep(min(delay, 0.02))
        except Exception:  # noqa: BLE001
            pass
        finally:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self.finished.emit(gen)
