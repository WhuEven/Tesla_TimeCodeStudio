"""TimeCode Studio 主窗口。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDesktopServices, QPixmap, QIntValidator
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QApplication, QAbstractItemView,
    QHeaderView,
)

from . import fontgen
from .assgen import OSSStyle, build_ass
from .exporter import ExportRunner, build_plan, resolve_output_size
from .media import extract_frame, find_ffmpeg, find_ffprobe, probe
from .model import (
    Clip, ExportSettings, Project, fmt_hms, human_size, nice_bitrate_for,
    parse_hms,
)
from .naming import TESLA_VIEW_ORDER, VIEW_LABELS, fill_missing_times, sort_for_timeline

APP_DIR = Path(__file__).resolve().parent.parent
FONT_DIR = APP_DIR / "assets" / "fonts"

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".ts", ".mpg", ".mpeg"}

COL_CHECK, COL_IDX, COL_NAME, COL_VIEW, COL_START, COL_IN, COL_OUT, COL_DUR, COL_SPEC = range(9)


# ------------------------------------------------------------------ 字体选择

def scan_system_fonts() -> list[tuple[str, Path]]:
    """扫描系统字体目录，返回 (显示名, 路径)。"""
    roots = [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
             Path.home() / "Library" / "Fonts"]
    if os.name == "nt":
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.[to]tf")) + sorted(root.glob("*.ttc")):
            found.setdefault(p.stem, p)
    return sorted(found.items())


def font_family_of(path: Path) -> str:
    try:
        from fontTools.ttLib import TTFont

        f = TTFont(str(path), fontNumber=0, lazy=True)
        name = f["name"].getDebugName(1) or path.stem
        f.close()
        return name
    except Exception:
        return path.stem


# ------------------------------------------------------------------ 导出线程

class ExportWorker(QThread):
    progress = Signal(float)
    log = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.runner: ExportRunner | None = None

    def run(self):
        self.runner = ExportRunner(
            self.plan,
            on_progress=lambda f: self.progress.emit(float(f)),
            on_log=lambda s: self.log.emit(s),
        )
        try:
            out = self.runner.run()
            self.done.emit(str(out))
        except InterruptedError:
            self.failed.emit("已取消")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def cancel(self):
        if self.runner:
            self.runner.cancel()


# ------------------------------------------------------------------ 裁剪对话框

class TrimDialog(QDialog):
    """单片段截取：预览帧 + 入/出点滑块。"""

    def __init__(self, clip: Clip, ffmpeg: str, parent=None):
        super().__init__(parent)
        self.clip = clip
        self.ffmpeg = ffmpeg
        self.tmp = Path(tempfile.mkdtemp(prefix="tcs_preview_"))
        self.setWindowTitle(f"截取 — {clip.path.name}")
        self.resize(640, 520)

        self.lbl_frame = QLabel("正在抽帧…")
        self.lbl_frame.setAlignment(Qt.AlignCenter)
        self.lbl_frame.setMinimumSize(560, 300)
        self.lbl_frame.setStyleSheet("background:#111;color:#aaa;")

        dur = clip.duration
        self.sld_pos = QSlider(Qt.Horizontal); self.sld_pos.setRange(0, int(dur * 10))
        self.sld_in = QSlider(Qt.Horizontal); self.sld_in.setRange(0, int(dur * 10))
        self.sld_out = QSlider(Qt.Horizontal); self.sld_out.setRange(0, int(dur * 10))
        self.sld_in.setValue(int(clip.in_point * 10))
        self.sld_out.setValue(int((clip.out_point or dur) * 10))
        self.sld_pos.setValue(int((clip.in_point + 1) * 10))

        self.lbl_info = QLabel()
        b1 = QPushButton("把当前位置设为入点")
        b2 = QPushButton("把当前位置设为出点")
        b1.clicked.connect(lambda: self.sld_in.setValue(self.sld_pos.value()))
        b2.clicked.connect(lambda: self.sld_out.setValue(self.sld_pos.value()))
        for s in (self.sld_pos, self.sld_in, self.sld_out):
            s.valueChanged.connect(self._sync)

        ok = QPushButton("确定"); ok.clicked.connect(self.accept)
        ca = QPushButton("取消"); ca.clicked.connect(self.reject)

        grid = QHBoxLayout()
        grid.addWidget(b1); grid.addWidget(b2); grid.addStretch(1); grid.addWidget(ok); grid.addWidget(ca)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lbl_frame)
        for label, s in (("预览位置", self.sld_pos), ("入点", self.sld_in), ("出点", self.sld_out)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label)); row.addWidget(s)
            lay.addLayout(row)
        lay.addWidget(self.lbl_info)
        lay.addLayout(grid)
        self._sync()
        self.sld_pos.setValue(int(dur * 10 * 0.3))

    def _sync(self):
        self.sld_in.setMaximum(max(0, self.sld_out.value() - 1))
        self.sld_out.setMinimum(self.sld_in.value() + 1)
        p = self.sld_pos.value() / 10
        i, o = self.sld_in.value() / 10, self.sld_out.value() / 10
        self.lbl_info.setText(
            f"入点 {fmt_hms(i, True)}   出点 {fmt_hms(o, True)}   截取时长 {o - i:.2f}s")
        out = self.tmp / "f.png"
        if extract_frame(self.ffmpeg, self.clip.path, p, out, width=560):
            pm = QPixmap(str(out))
            if not pm.isNull():
                self.lbl_frame.setPixmap(
                    pm.scaled(self.lbl_frame.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def apply(self):
        self.clip.in_point = self.sld_in.value() / 10
        self.clip.out_point = self.sld_out.value() / 10


# ------------------------------------------------------------------ 主窗口

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.ffmpeg = find_ffmpeg() or ""
        self.probe_bin = find_ffprobe()
        self.worker: ExportWorker | None = None
        self._loading = False
        self.fontgen_done = False

        self.setWindowTitle("TimeCode Studio — 行车记录仪 / 监控时间码工具")
        self.resize(1280, 800)
        self._build_ui()
        self._update_estimate()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        split = QSplitter(Qt.Horizontal, central)
        lay = QVBoxLayout(central); lay.addWidget(split)

        # ---- 左侧：片段列表 ----
        left = QWidget(); lv = QVBoxLayout(left)
        bar = QHBoxLayout()
        b1 = QPushButton("导入文件…"); b1.clicked.connect(self.import_files)
        b2 = QPushButton("导入文件夹…"); b2.clicked.connect(self.import_folder)
        self.btn_sort = QPushButton("⇅ 按时间排序"); self.btn_sort.clicked.connect(self.sort_clips)
        self.btn_trim = QPushButton("✂ 截取片段…"); self.btn_trim.clicked.connect(self.trim_selected)
        self.btn_del = QPushButton("移除所选"); self.btn_del.clicked.connect(self.remove_selected)
        for b in (b1, b2, self.btn_sort, self.btn_trim, self.btn_del):
            bar.addWidget(b)
        bar.addStretch(1)
        lv.addLayout(bar)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("启用视角:"))
        self.cmb_view = QComboBox(); self.cmb_view.currentTextChanged.connect(self.apply_view_filter)
        filter_row.addWidget(self.cmb_view); filter_row.addStretch(1)
        lv.addLayout(filter_row)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["启用", "#", "文件名", "视角", "时间码起点", "入点", "出点", "截取时长", "规格"])
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col, wd in ((COL_CHECK, 46), (COL_IDX, 32), (COL_VIEW, 84),
                        (COL_START, 150), (COL_IN, 92), (COL_OUT, 92),
                        (COL_DUR, 74), (COL_SPEC, 150)):
            self.table.setColumnWidth(col, wd)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.dragEnterEvent = self._drag_enter
        self.table.dropEvent = self._drop
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        lv.addWidget(self.table)

        # ---- 右侧：设置 ----
        right = QWidget(); rv = QVBoxLayout(right)
        right.setFixedWidth(360)

        osd_box = QGroupBox("时间码 OSD（右下角）")
        ov = QVBoxLayout(osd_box)
        self.chk_osd = QCheckBox("叠加时间码"); self.chk_osd.setChecked(True)
        self.chk_osd.toggled.connect(self._update_estimate)
        ov.addWidget(self.chk_osd)
        self.edt_loc = QLineEdit(); self.edt_loc.setPlaceholderText("第二行文本（可选，留空不显示）")
        ov.addWidget(self.edt_loc)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("字体:"))
        self.cmb_font = QComboBox(); self.cmb_font.addItem("内置监控点阵 (TC OSD Dot)", None)
        for name, path in scan_system_fonts():
            self.cmb_font.addItem(name, str(path))
        frow.addWidget(self.cmb_font, 1); ov.addLayout(frow)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("字号"))
        self.sld_size = QSlider(Qt.Horizontal); self.sld_size.setRange(15, 90); self.sld_size.setValue(40)
        self.lbl_size = QLabel(); self.lbl_size.setMinimumWidth(60)
        srow.addWidget(self.sld_size, 1); srow.addWidget(self.lbl_size)
        ov.addLayout(srow)
        self.chk_box = QCheckBox("半透明底板"); ov.addWidget(self.chk_box)
        self.btn_preview = QPushButton("👁 预览 OSD 效果"); self.btn_preview.clicked.connect(self.preview_osd)
        ov.addWidget(self.btn_preview)
        rv.addWidget(osd_box)

        exp_box = QGroupBox("导出设置")
        ev = QFormLayout(exp_box)
        self.cmb_res = QComboBox()
        for label, data in (("跟随源分辨率", None), ("1080p", (1920, 1080)),
                            ("720p", (1280, 720)), ("540p", (960, 540)), ("480p", (640, 480))):
            self.cmb_res.addItem(label, data)
        self.cmb_res.currentIndexChanged.connect(self._on_res_changed)
        ev.addRow("分辨率", self.cmb_res)
        self.cmb_enc = QComboBox()
        self.cmb_enc.addItem("H.264 软编码 libx264（质量最佳）", "libx264")
        self.cmb_enc.addItem("H.264 硬件加速 VideoToolbox（最快）", "h264_videotoolbox")
        self.cmb_enc.currentIndexChanged.connect(self._on_enc_changed)
        ev.addRow("编码器", self.cmb_enc)
        self.cmb_rate = QComboBox()
        for label, kbps in (("高码率 12 Mbps", 12000), ("标准 8 Mbps", 8000),
                            ("平衡 6 Mbps", 6000), ("压缩 4 Mbps", 4000),
                            ("高压缩 2.5 Mbps", 2500), ("自定义…", 0)):
            self.cmb_rate.addItem(label, kbps)
        self.cmb_rate.currentIndexChanged.connect(self._on_rate_changed)
        ev.addRow("压缩码率", self.cmb_rate)
        self.spin_rate = QSpinBox(); self.spin_rate.setRange(200, 120000); self.spin_rate.setValue(6000)
        self.spin_rate.setSuffix(" kbps"); self.spin_rate.valueChanged.connect(self._update_estimate)
        ev.addRow("自定义码率", self.spin_rate)
        self.spin_audio = QSpinBox(); self.spin_audio.setRange(32, 320); self.spin_audio.setValue(128)
        self.spin_audio.setSuffix(" kbps"); self.spin_audio.valueChanged.connect(self._update_estimate)
        ev.addRow("音频码率", self.spin_audio)
        rv.addWidget(exp_box)

        run_box = QGroupBox("导出")
        rvv = QVBoxLayout(run_box)
        self.lbl_stats = QLabel("未导入片段")
        self.lbl_stats.setWordWrap(True)
        rvv.addWidget(self.lbl_stats)
        self.lbl_estimate = QLabel("—")
        f = self.lbl_estimate.font(); f.setPointSize(f.pointSize() + 3); f.setBold(True)
        self.lbl_estimate.setFont(f)
        rvv.addWidget(self.lbl_estimate)
        self.btn_export = QPushButton("导出视频…"); self.btn_export.clicked.connect(self.do_export)
        self.btn_cancel = QPushButton("取消"); self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        rr = QHBoxLayout(); rr.addWidget(self.btn_export, 2); rr.addWidget(self.btn_cancel, 1)
        rvv.addLayout(rr)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setValue(0)
        rvv.addWidget(self.progress)
        self.txt_log = QPlainTextEdit(); self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(110)
        self.txt_log.setPlaceholderText("导出日志…")
        rvv.addWidget(self.txt_log)
        rv.addWidget(run_box)
        rv.addStretch(1)

        copy = QLabel("© 2026 Haonan Zhu · 保留所有权利 · 商用需书面授权")
        copy.setStyleSheet("color:#888;font-size:11px;")
        copy.setAlignment(Qt.AlignCenter)
        rv.addWidget(copy)

        split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(0, 1)

        self.statusBar().showMessage(
            f"ffmpeg: {self.ffmpeg or '未找到'}" + ("（含 ffprobe）" if self.probe_bin else "（用 ffmpeg 探测媒体信息）"))

    # ---------------- 导入 ----------------
    def import_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择视频文件", "",
                                                "视频文件 (*.mp4 *.mov *.m4v *.avi *.mkv *.ts);;所有文件 (*)")
        if files:
            self._import(files)

    def import_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（如 TeslaCam/SavedClips 或事件目录）")
        if not d:
            return
        root = Path(d)
        files = sorted(p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXT)
        if not files:
            QMessageBox.information(self, "提示", "该目录下未找到视频文件")
            return
        self._import([str(p) for p in files])

    def _import(self, paths):
        prog = QProgressBar(self); prog.setRange(0, len(paths))
        dlg = QDialog(self); dlg.setWindowTitle("正在读取媒体信息…")
        dl = QVBoxLayout(dlg); dl.addWidget(QLabel("正在探测视频文件，请稍候…")); dl.addWidget(prog)
        dlg.show()
        added = skipped = 0
        exist = {str(c.path) for c in self.project.clips}
        QApplication.processEvents()
        for i, p in enumerate(paths):
            prog.setValue(i); QApplication.processEvents()
            pp = Path(p)
            if str(pp) in exist or pp.suffix.lower() not in VIDEO_EXT or not pp.exists():
                skipped += 1
                continue
            try:
                info = probe(pp)
            except Exception:
                skipped += 1
                continue
            if info.duration <= 0:
                skipped += 1
                continue
            clip = Clip.from_path(pp)
            clip.info = info
            clip.out_point = info.duration
            if clip.time_source == "未识别" and info.location:
                pass
            self.project.clips.append(clip)
            exist.add(str(pp))
            added += 1
        prog.setValue(len(paths)); dlg.accept()

        fill_missing_times(self.project.clips)
        self.refresh_view_combo()
        self.sort_clips(silent=True)
        self._auto_pick_bitrate()
        if not self.edt_loc.text():
            loc = next((c.info.location for c in self.project.clips if c.info.location), "")
            self.edt_loc.setText(loc)
        self.statusBar().showMessage(f"导入完成：新增 {added}，跳过 {skipped}（重复/不支持）", 8000)
        self.refresh_table()
        self._update_estimate()

    def _drag_enter(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        dirs = [f for f in files if Path(f).is_dir()]
        files = [f for f in files if Path(f).is_file()]
        for d in dirs:
            files += [str(p) for p in Path(d).rglob("*") if p.suffix.lower() in VIDEO_EXT]
        if files:
            self._import(files)

    # ---------------- 列表操作 ----------------
    def refresh_view_combo(self):
        views = []
        for c in self.project.clips:
            if c.view and c.view not in views:
                views.append(c.view)
        views.sort(key=lambda v: TESLA_VIEW_ORDER.index(v) if v in TESLA_VIEW_ORDER else 99)
        cur = self.cmb_view.currentText()
        self.cmb_view.blockSignals(True)
        self.cmb_view.clear()
        self.cmb_view.addItem("全部")
        for v in views:
            self.cmb_view.addItem(VIEW_LABELS.get(v, v), v)
        if cur:
            idx = self.cmb_view.findText(cur)
            if idx >= 0:
                self.cmb_view.setCurrentIndex(idx)
        self.cmb_view.blockSignals(False)

    def apply_view_filter(self, _text):
        data = self.cmb_view.currentData()
        for c in self.project.clips:
            c.enabled = data is None or c.view == data
        self.refresh_table()
        self._update_estimate()

    def sort_clips(self, checked=False, silent=False):
        self.project.clips = sort_for_timeline(self.project.clips)
        self.refresh_table()
        if not silent:
            self._update_estimate()

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.project.clips[r]
        self.refresh_table()
        self.refresh_view_combo()
        self._update_estimate()

    def trim_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows or not self.ffmpeg:
            return
        for r in rows:
            dlg = TrimDialog(self.project.clips[r], self.ffmpeg, self)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply()
        self.refresh_table()
        self._update_estimate()

    def refresh_table(self):
        self._loading = True
        clips = self.project.clips
        self.table.setRowCount(len(clips))
        for i, c in enumerate(clips):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if c.enabled else Qt.Unchecked)
            self.table.setItem(i, COL_CHECK, it)
            self.table.setItem(i, COL_IDX, self._ro(str(i + 1)))
            name = QTableWidgetItem(c.path.name)
            name.setToolTip(str(c.path))
            self.table.setItem(i, COL_NAME, name)
            self.table.setItem(i, COL_VIEW, self._ro(c.view and VIEW_LABELS.get(c.view, c.view) or "—"))
            self.table.setItem(i, COL_START, QTableWidgetItem(c.start_text()))
            self.table.setItem(i, COL_IN, QTableWidgetItem(fmt_hms(c.in_point, True)))
            self.table.setItem(i, COL_OUT, QTableWidgetItem(fmt_hms(c.out_point, True)))
            self.table.setItem(i, COL_DUR, self._ro(f"{c.clip_duration:.1f}s"))
            self.table.setItem(i, COL_SPEC, self._ro(
                f"{c.info.width}×{c.info.height} {c.info.fps:.0f}fps" +
                ("" if c.info.has_audio else " · 静音")))
        self._loading = False

    @staticmethod
    def _ro(text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        return it

    def _on_item_changed(self, item):
        if self._loading:
            return
        row = item.row()
        clips = self.project.clips
        if row >= len(clips):
            return
        c = clips[row]
        text = item.text().strip()
        if item.column() == COL_CHECK:
            c.enabled = item.checkState() == Qt.Checked
        elif item.column() == COL_START:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
                try:
                    c.start_dt = datetime.strptime(text, fmt)
                    c.time_source = "手动"
                    break
                except ValueError:
                    continue
            self._loading = True
            item.setText(c.start_text()); self._loading = False
        elif item.column() in (COL_IN, COL_OUT):
            v = parse_hms(text)
            if v is None:
                self._loading = True
                item.setText(fmt_hms(c.in_point if item.column() == COL_IN else c.out_point, True))
                self._loading = False
                return
            v = max(0.0, min(v, c.duration))
            if item.column() == COL_IN:
                c.in_point = v
                item.setText(fmt_hms(v, True))
            else:
                c.out_point = v
                item.setText(fmt_hms(v, True))
        elif item.column() == COL_DUR:
            return
        self._loading = True
        self.table.item(row, COL_DUR).setText(f"{c.clip_duration:.1f}s")
        self._loading = False
        self._update_estimate()

    def _sel_changed(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        self.btn_trim.setEnabled(bool(rows))
        self.btn_del.setEnabled(bool(rows))

    # ---------------- OSD / 预估 ----------------
    def _osd_style(self, out_h: int) -> OSSStyle:
        st = OSSStyle()
        st.enabled = self.chk_osd.isChecked()
        st.size_ratio = self.sld_size.value() / 1000.0
        st.box = self.chk_box.isChecked()
        st.location = self.edt_loc.text().strip()
        st.font_name = "TC OSD Dot"
        path = self.cmb_font.currentData()
        if path:
            st.font_name = font_family_of(Path(path))
        return st

    def _export_settings(self) -> ExportSettings:
        s = ExportSettings()
        res = self.cmb_res.currentData()
        if res:
            s.width, s.height = res
        s.encoder = self.cmb_enc.currentData() or "libx264"
        if self.cmb_enc.currentData() == "h264_videotoolbox":
            s.preset = ""
        rate = self.cmb_rate.currentData()
        s.video_bitrate_kbps = self.spin_rate.value() if rate == 0 else rate
        s.audio_bitrate_kbps = self.spin_audio.value()
        s.include_audio = any(c.info.has_audio for c in self.project.active_clips())
        return s

    def _auto_pick_bitrate(self):
        """按输出分辨率把码率预设调到最接近的推荐值（仅在未选自定义时）。"""
        if self.cmb_rate.currentData() == 0:
            return
        try:
            from .exporter import resolve_output_size

            w, h = resolve_output_size(self.project, self._export_settings())
        except Exception:
            return
        want = nice_bitrate_for(w, h)
        best, idx = None, 0
        for i in range(self.cmb_rate.count()):
            kbps = self.cmb_rate.itemData(i)
            if not kbps:
                continue
            d = abs(kbps - want)
            if best is None or d < best:
                best, idx = d, i
        self.cmb_rate.setCurrentIndex(idx)

    def _on_res_changed(self):
        """分辨率变化后重推码率并刷新预估。"""
        self._auto_pick_bitrate()
        self._update_estimate()

    def _on_enc_changed(self):
        hw = self.cmb_enc.currentData() == "h264_videotoolbox"
        self.spin_rate.setEnabled(True)

    def _on_rate_changed(self):
        custom = self.cmb_rate.currentData() == 0
        self.spin_rate.setEnabled(custom)
        if not custom:
            self._update_estimate()

    def _ensure_font(self):
        if not self.fontgen_done:
            try:
                fontgen.ensure_font(FONT_DIR)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "字体生成失败", f"内置点阵字体生成失败：{exc}")
            self.fontgen_done = True

    def _update_estimate(self):
        total = self.project.total_duration()
        n = len(self.project.active_clips())
        self.lbl_size.setText(f"{self.sld_size.value()/1000:.3f}×H")
        if n == 0:
            self.lbl_stats.setText("未导入片段")
            self.lbl_estimate.setText("—")
            return
        s = self._export_settings()
        w, h = resolve_output_size(self.project, s)
        fps = self.project.dominant_fps()
        self.lbl_stats.setText(
            f"{n} 段 · 总时长 {fmt_hms(total, True)} · 输出 {w}×{h} @ {fps:.0f}fps")
        est = s.target_size_bytes(total)
        self.lbl_estimate.setText(f"预估体积 ≈ {human_size(est)}")
        self.btn_export.setEnabled(True)

    # ---------------- 预览 ----------------
    def preview_osd(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "先在列表中选择一个片段再预览")
            return
        self._ensure_font()
        clip = self.project.clips[rows[0]]
        work = Path(tempfile.mkdtemp(prefix="tcs_osd_"))
        at = (clip.in_point + min(clip.out_point, clip.duration)) / 2
        st = self._osd_style(clip.info.height)
        st.location = self.edt_loc.text().strip()
        fonts = work / "fonts"; fonts.mkdir()
        fpath = self.cmb_font.currentData()
        if fpath:
            shutil.copy(fpath, fonts / Path(fpath).name)
        else:
            shutil.copy(fontgen.ensure_font(FONT_DIR), fonts / "TCOSDDot-Regular.ttf")
        w, h = clip.info.width, clip.info.height
        segs = []
        from .model import RenderSegment
        segs.append(RenderSegment(0.0, clip.duration, 0.0, clip.start_dt or datetime.now()))
        ass = build_ass(segs, w, h, st, work / "preview.ass")
        ff = self.ffmpeg
        # 只渲染该帧附近的 OSD（字幕事件覆盖全片，直接渲染那一帧）
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{at:.3f}", "-i", str(clip.path),
               "-vf", f"ass=filename={ass.name}:fontsdir=fonts",
               "-frames:v", "1", str(work / "preview.png")]
        proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, timeout=120)
        png = work / "preview.png"
        if not png.exists():
            QMessageBox.warning(self, "预览失败", proc.stderr or "未知错误")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"OSD 预览 — {clip.path.name} @ {fmt_hms(at, True)}")
        lab = QLabel()
        pm = QPixmap(str(png))
        lab.setPixmap(pm.scaled(1000, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay = QVBoxLayout(dlg); lay.addWidget(lab)
        dlg.exec()

    # ---------------- 导出 ----------------
    def do_export(self):
        if not self.ffmpeg:
            QMessageBox.critical(self, "错误", "未找到 ffmpeg，请先安装：\nbrew install ffmpeg")
            return
        clips = self.project.active_clips()
        if not clips:
            QMessageBox.information(self, "提示", "没有可导出的片段")
            return
        self._ensure_font()
        first = clips[0]
        suffix = f"-{first.view}" if first.view else ""
        base = first.start_dt.strftime("%Y%m%d_%H%M%S") if first.start_dt else "export"
        out, _ = QFileDialog.getSaveFileName(
            self, "保存导出文件", str(Path.home() / "Desktop" / f"TC_{base}{suffix}.mp4"),
            "MP4 视频 (*.mp4)")
        if not out:
            return
        work = Path(tempfile.mkdtemp(prefix="tcs_export_"))
        fonts = work / "fonts"; fonts.mkdir()
        fpath = self.cmb_font.currentData()
        if fpath:
            shutil.copy(fpath, fonts / Path(fpath).name)
        else:
            shutil.copy(fontgen.ensure_font(FONT_DIR), fonts / "TCOSDDot-Regular.ttf")

        s = self._export_settings()
        style = self._osd_style(720)
        try:
            plan = build_plan(self.project, s, style, work, Path(out), self.ffmpeg, fonts)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "无法导出", str(exc))
            return

        self.btn_export.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.txt_log.clear()
        self.txt_log.appendPlainText(plan.summary())
        self.worker = ExportWorker(plan)
        self.worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self.worker.log.connect(lambda line: self.txt_log.appendPlainText(line))
        self.worker.done.connect(self._export_done)
        self.worker.failed.connect(self._export_failed)
        self.worker.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)

    def _export_done(self, out: str):
        self.progress.setValue(1000)
        self.btn_export.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        size = human_size(Path(out).stat().st_size)
        self.statusBar().showMessage(f"导出完成：{out}（{size}）")
        self.txt_log.appendPlainText(f"完成：{out}（{size}）")
        QMessageBox.information(self, "导出完成", f"已保存到：\n{out}\n\n大小：{size}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out).parent)))

    def _export_failed(self, msg: str):
        self.btn_export.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.txt_log.appendPlainText(f"[失败] {msg}")
        if msg != "已取消":
            QMessageBox.critical(self, "导出失败", msg)


def run():
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("TimeCode Studio")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
