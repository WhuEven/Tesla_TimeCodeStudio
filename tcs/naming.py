"""从文件名解析拍摄起始时间与摄像头视角。

TeslaCam 的实际命名（已在真实 U 盘上核实）：

    TeslaCam/SavedClips/2026-10-29_19-13-46/
        2026-10-29_19-03-40-front.mp4
        2026-10-29_19-03-40-back.mp4
        2026-10-29_19-03-40-left_repeater.mp4
        ...

即 ``YYYY-MM-DD_HH-MM-SS-<view>.mp4``。同名不同视角是同一时刻的 6 路画面，
每段时长约 60 秒，相邻两段的文件名时间戳间隔约 61 秒（中间约 1 秒是段落
缝隙）。因此时间码必须按"每个文件自己的文件名时间戳"计算，而不能按拼接后
的累计时长推算 —— 否则缝隙会累积成肉眼可见的时间漂移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 视角定义

VIEW_LABELS = {
    "front": "前视",
    "back": "后视",
    "left_repeater": "左翼子板",
    "right_repeater": "右翼子板",
    "left_pillar": "左B柱",
    "right_pillar": "右B柱",
}

# 特斯拉的视角顺序（导出多画面拼版时按此从左到右排列）
TESLA_VIEW_ORDER = [
    "front",
    "left_repeater",
    "right_repeater",
    "back",
    "left_pillar",
    "right_pillar",
]

_VIEW_ALIASES = {
    "front": "front",
    "back": "back",
    "rear": "back",
    "left_repeater": "left_repeater",
    "right_repeater": "right_repeater",
    "left_pillar": "left_pillar",
    "right_pillar": "right_pillar",
    "left": "left_repeater",
    "right": "right_repeater",
}

# ---------------------------------------------------------------- 时间解析

# 2026-10-29_19-03-40  /  2026-10-29 19.03.40  /  2026-10-29-19-03-40
_SEP_FULL = re.compile(
    r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ _T-]+(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})"
)
# 20261029_190340 / 20261029190340
_COMPACT_FULL = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})")
# 只有日期：20261029 / 2026-10-29
_DATE_ONLY = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


@dataclass
class NameInfo:
    """文件名解析结果。"""

    start: datetime | None = None
    view: str | None = None
    source: str = "none"  # tesla / generic / none

    @property
    def view_label(self) -> str:
        if not self.view:
            return ""
        return VIEW_LABELS.get(self.view, self.view)

    @property
    def ok(self) -> bool:
        return self.start is not None


def _split_view(stem: str) -> str | None:
    """从文件名尾部取视角标记，如 ``...-front`` / ``..._left_repeater``。"""
    low = stem.lower()
    # 长标记优先，避免 left 抢先匹配 left_repeater
    for key in sorted(_VIEW_ALIASES, key=len, reverse=True):
        if re.search(rf"[-_.]{re.escape(key)}$", low):
            return _VIEW_ALIASES[key]
    return None


def _build(y, mo, d, h, mi, s) -> datetime | None:
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None


def parse_name(path: str | Path) -> NameInfo:
    """解析文件名的起始时间与视角，解析不到时间时 ``start`` 为 None。"""
    stem = Path(path).stem
    view = _split_view(stem)

    m = _SEP_FULL.search(stem)
    if m:
        dt = _build(*m.groups())
        if dt:
            return NameInfo(dt, view, "tesla" if view else "generic")

    m = _COMPACT_FULL.search(stem)
    if m:
        dt = _build(*m.groups())
        if dt:
            return NameInfo(dt, view, "tesla" if view else "generic")

    m = _DATE_ONLY.search(stem)
    if m:
        dt = _build(*m.groups(), 0, 0, 0)
        if dt:
            return NameInfo(dt, view, "generic")

    return NameInfo(None, view, "none")


def guess_from_mtime(path: str | Path) -> datetime:
    """兜底：用文件修改时间（对拷贝过的文件通常接近录制时间）。"""
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except OSError:
        return datetime.now()


def fill_missing_times(items) -> None:
    """给解析不到时间的片段按相邻片段推算起点。

    规则：优先用前一个片段的 起始时间 + 时长 接续；否则用后一个片段倒推。
    这样即使部分文件名被改名，整条时间轴仍然连续。
    """
    for i, it in enumerate(items):
        if it.start_dt is not None:
            continue
        prev = next((p for p in reversed(items[:i]) if p.start_dt is not None), None)
        if prev is not None:
            it.start_dt = prev.start_dt + timedelta(seconds=prev.out_point or prev.duration)
            it.time_source = "推算"
            continue
        nxt = next((n for n in items[i + 1 :] if n.start_dt is not None), None)
        if nxt is not None:
            it.start_dt = nxt.start_dt - timedelta(seconds=it.duration)
            it.time_source = "推算"
            continue
        it.start_dt = guess_from_mtime(it.path)
        it.time_source = "文件时间"


def sort_for_timeline(items) -> list:
    """按起始时间 + 视角稳定排序。"""
    def key(it):
        return (it.start_dt or datetime.min, TESLA_VIEW_ORDER.index(it.view)
                if it.view in TESLA_VIEW_ORDER else 99, str(it.path))

    return sorted(items, key=key)
