"""单条时间轴控件：把入点、出点、预览点画在同一条轨道上。

旧实现用三个独立 QSlider 表示入点 / 出点 / 预览位置。三者各自动态改
``setMinimum`` / ``setMaximum``，滑块手柄是按各自的 [min, max] 归一化绘制的，
所以改一个滑块会让另一个手柄在视觉上"跳"一下，用户感知为「设入点时出点也变了」。
本控件把三个点放在同一坐标系里，谁动就只动谁：
拖动入点手柄会停在出点前（不会推出点），拖动出点手柄会停在入点后。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .model import fmt_hms

_TICK_STEPS = (0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)


class TimelineBar(QWidget):
    """一条轨道 + 入 / 出 / 预览三个标记。"""

    MARGIN = 16.0
    TRACK_TOP = 20.0
    TRACK_H = 18.0
    HANDLE_W = 7.0
    HIT = 9.0          # 手柄命中半径（像素）

    cursorMoved = Signal(float)          # 预览点移动
    rangeChanged = Signal(float, float)  # 入 / 出点变化

    def __init__(self, duration: float, fps: float = 30.0, parent=None):
        super().__init__(parent)
        self._dur = max(0.02, float(duration))
        self._fps = float(fps) if fps and fps > 0 else 30.0
        self._gap = min(self._dur * 0.5, max(1e-3, 1.0 / self._fps))
        self._in = 0.0
        self._out = self._dur
        self._cursor = 0.0
        self._drag: str | None = None
        self.setMinimumHeight(74)
        self.setMaximumHeight(74)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # ---------------- 取值 / 赋值 ----------------
    def duration(self) -> float:
        return self._dur

    def in_point(self) -> float:
        return self._in

    def out_point(self) -> float:
        return self._out

    def cursor(self) -> float:
        return self._cursor

    def cursor_inside(self) -> bool:
        return self._in - 1e-9 <= self._cursor <= self._out + 1e-9

    def frame_step(self) -> float:
        return 1.0 / self._fps

    def snap(self, t: float) -> float:
        """吸附到帧边界并限制在 [0, 时长]。"""
        t = min(self._dur, max(0.0, float(t)))
        if self._fps > 0:
            t = round(t * self._fps) / self._fps
        return min(self._dur, max(0.0, t))

    def set_cursor(self, t: float, emit: bool = True):
        t = self.snap(t)
        if abs(t - self._cursor) < 1e-9:
            return
        self._cursor = t
        self.update()
        if emit:
            self.cursorMoved.emit(t)

    def nudge_cursor(self, delta: float):
        self.set_cursor(self._cursor + delta)

    def set_range(self, a: float, b: float, emit: bool = True):
        a, b = self.snap(a), self.snap(b)
        if b - a < self._gap:
            b = min(self._dur, a + self._gap)
            if b - a < self._gap:            # 顶到片尾时改为往前收
                a = max(0.0, b - self._gap)
        changed = abs(a - self._in) > 1e-9 or abs(b - self._out) > 1e-9
        self._in, self._out = a, b
        self.update()
        if changed and emit:
            self.rangeChanged.emit(a, b)

    def set_in(self, t: float, emit: bool = True) -> bool:
        """设置入点，永远不动出点；超出时夹在出点之前。返回是否被夹。"""
        t = self.snap(max(0.0, t))
        limit = max(0.0, self._out - self._gap)
        clamped = t > limit + 1e-9
        self.set_range(min(t, limit), self._out, emit=emit)
        return clamped

    def set_out(self, t: float, emit: bool = True) -> bool:
        """设置出点，永远不动入点；超出时夹在入点之后。返回是否被夹。"""
        t = self.snap(min(self._dur, t))
        limit = min(self._dur, self._in + self._gap)
        clamped = t < limit - 1e-9
        self.set_range(self._in, max(t, limit), emit=emit)
        return clamped

    def set_in_from_cursor(self) -> bool:
        return self.set_in(self._cursor)

    def set_out_from_cursor(self) -> bool:
        return self.set_out(self._cursor)

    # ---------------- 坐标换算 ----------------
    def _x0(self) -> float:
        return self.MARGIN

    def _x1(self) -> float:
        return max(self.MARGIN + 1.0, self.width() - self.MARGIN)

    def _x(self, t: float) -> float:
        x0, x1 = self._x0(), self._x1()
        return x0 + (x1 - x0) * min(1.0, max(0.0, t / self._dur))

    def _t(self, x: float) -> float:
        x0, x1 = self._x0(), self._x1()
        return min(1.0, max(0.0, (x - x0) / max(1e-6, x1 - x0))) * self._dur

    # ---------------- 鼠标 ----------------
    def _hit_handle(self, x: float) -> str | None:
        d_in = abs(x - self._x(self._in))
        d_out = abs(x - self._x(self._out))
        best = min(d_in, d_out)
        if best > self.HIT:
            return None
        return "in" if d_in <= d_out else "out"

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        handle = self._hit_handle(x)
        if handle:
            self._drag = handle
        else:
            self._drag = "cursor"
            self.set_cursor(self._t(x))
        self.update()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._drag == "in":
            self.set_in(self._t(x))
        elif self._drag == "out":
            self.set_out(self._t(x))
        elif self._drag == "cursor":
            self.set_cursor(self._t(x))
        else:
            handle = self._hit_handle(x)
            self.setCursor(Qt.SplitHCursor if handle else Qt.PointingHandCursor)
            return
        self.setCursor(Qt.SplitHCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.update()

    def leaveEvent(self, event):
        if self._drag is None:
            self.unsetCursor()
        super().leaveEvent(event)

    # ---------------- 绘制 ----------------
    def _tick_step(self) -> float:
        span = self._x1() - self._x0()
        target = max(2.0, span / 92.0)
        raw = self._dur / target
        for c in _TICK_STEPS:
            if raw <= c:
                return c
        return _TICK_STEPS[-1]

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        light = self.palette().color(QPalette.Window).lightness() > 128

        c_track = QColor("#E3E7ED" if light else "#2B3037")
        c_edge = QColor("#C6CCD6" if light else "#3B424B")
        c_sel = QColor(47, 124, 246, 70 if light else 96)
        c_accent = QColor("#2F7CF6" if light else "#4C9AFF")
        c_muted = QColor("#7A8291" if light else "#8E97A5")
        c_text = QColor("#111827" if light else "#F0F3F8")
        c_warn = QColor("#E07B39" if light else "#F09A5A")

        x0, x1 = self._x0(), self._x1()
        ty, th = self.TRACK_TOP, self.TRACK_H

        # ---- 刻度 ----
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1.0))
        p.setFont(f)
        step = self._tick_step()
        ticks = []
        t = 0.0
        while t <= self._dur + 1e-6:
            ticks.append(t)
            t += step
        for tv in ticks:
            x = self._x(tv)
            p.setPen(QPen(c_edge, 1))
            p.drawLine(int(x), int(ty - 6), int(x), int(ty - 2))
            if self._dur >= 60:
                label = fmt_hms(tv)
            elif step >= 1:
                label = f"{tv:.0f}s"
            else:
                label = f"{tv:.1f}s"
            p.setPen(c_muted)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label)
            lx = min(max(x - tw / 2, 0.0), self.width() - tw)
            p.drawText(int(lx), int(ty - 9), label)

        # ---- 轨道 ----
        track = QRectF(x0, ty, x1 - x0, th)
        p.setPen(QPen(c_edge, 1))
        p.setBrush(c_track)
        p.drawRoundedRect(track, 4, 4)

        # ---- 截取范围 ----
        xi, xo = self._x(self._in), self._x(self._out)
        sel = QRectF(xi, ty, max(1.0, xo - xi), th)
        p.setPen(Qt.NoPen)
        p.setBrush(c_sel)
        p.drawRect(sel)
        p.setBrush(c_accent)
        p.drawRect(QRectF(xi, ty + th - 3, max(1.0, xo - xi), 3))

        # ---- 出 / 入点手柄 ----
        for name, x in (("in", xi), ("out", xo)):
            active = self._drag == name
            col = QColor(c_accent)
            if active:
                col = col.lighter(115)
            hx = x - self.HANDLE_W / 2
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(hx, ty - 6, self.HANDLE_W, th + 12), 3, 3)
            p.setBrush(QColor("#FFFFFF" if light else "#0E1116"))
            p.drawRect(QRectF(hx + self.HANDLE_W / 2 - 0.5, ty - 1, 1.0, th + 2))

        # ---- 预览点 ----
        cx = self._x(self._cursor)
        inside = self.cursor_inside()
        pc = c_text if inside else c_warn
        pen = QPen(pc, 2)
        if not inside:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawLine(int(cx), int(ty - 10), int(cx), int(ty + th + 12))
        p.setPen(Qt.NoPen)
        p.setBrush(pc)
        head = QRectF(cx - 4.5, ty - 15, 9, 9)
        p.drawEllipse(head)

        # ---- 底部标签 ----
        f2 = QFont(self.font())
        f2.setPointSizeF(max(8.0, f2.pointSizeF() - 1.0))
        p.setFont(f2)
        fm = p.fontMetrics()
        base = self.height() - 8
        p.setPen(c_accent)
        tag_rects = []
        for tag, x in (("入", xi), ("出", xo)):
            tw = fm.horizontalAdvance(tag)
            tx = min(max(x - tw / 2, 0), self.width() - tw)
            tag_rects.append(QRectF(tx, base - fm.ascent(), tw, fm.height()))
            p.drawText(int(tx), base, tag)
        if not inside:
            tip = f"{fmt_hms(self._cursor, True)} · 范围外"
            tw = fm.horizontalAdvance(tip)
            tx = self.width() - self.MARGIN - tw
            tip_rect = QRectF(tx, base - fm.ascent(), tw, fm.height())
            if not any(tip_rect.intersects(r) for r in tag_rects):
                p.setPen(c_warn)
                p.drawText(int(tx), base, tip)
