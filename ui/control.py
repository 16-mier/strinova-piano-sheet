# -*- coding: utf-8 -*-
"""主控制窗 —— 选谱、播放、切换显示、摆悬浮窗的位置。"""

from __future__ import annotations

import json
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import layout, parser, timeline
from core.paths import APP_NAME, all_sheets, config_path, sheets_dir

from .editor import EditorDialog
from .overlay import OverlayWindow, Player


def _fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    return '%02d:%04.1f' % (m, sec - m * 60)


class ControlWindow(QMainWindow):
    def __init__(self, player: Player, overlay: OverlayWindow):
        super().__init__()
        self.player = player
        self.overlay = overlay
        self.tl: timeline.Timeline | None = None
        self.path: str | None = None

        self.setWindowTitle('%s — 控制台' % APP_NAME)
        self.resize(640, 680)

        self._build()
        self._wire()
        self._load_config()
        self._scan_sheets()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)

        # ---------- 谱面 ----------
        g_sheet = QGroupBox('谱面')
        f = QVBoxLayout(g_sheet)

        row = QHBoxLayout()
        self.cmb_sheet = QComboBox()
        self.cmb_sheet.setMinimumWidth(220)
        btn_open = QPushButton('打开…')
        btn_edit = QPushButton('编辑谱面')
        btn_reload = QPushButton('重新载入')
        for b in (btn_open, btn_edit, btn_reload):
            b.setFixedHeight(30)
        row.addWidget(QLabel('曲谱仓库'))
        row.addWidget(self.cmb_sheet, 1)
        row.addWidget(btn_open)
        row.addWidget(btn_edit)
        row.addWidget(btn_reload)
        f.addLayout(row)

        self.lbl_sheet = QLabel('还没有载入谱面')
        self.lbl_sheet.setWordWrap(True)
        self.lbl_sheet.setStyleSheet('color:#9aa3b8;')
        f.addWidget(self.lbl_sheet)

        self.btn_open, self.btn_edit, self.btn_reload = btn_open, btn_edit, btn_reload
        box.addWidget(g_sheet)

        # ---------- 播放 ----------
        g_play = QGroupBox('播放')
        fp = QVBoxLayout(g_play)

        prow = QHBoxLayout()
        self.btn_play = QPushButton('▶  播放')
        self.btn_stop = QPushButton('⏹  停止')
        self.btn_back = QPushButton('⏮  回到开头')
        self.btn_minus = QPushButton('◀  后退 0.5 秒')
        self.btn_plus = QPushButton('前进 0.5 秒  ▶')
        for b in (self.btn_play, self.btn_stop, self.btn_back,
                  self.btn_minus, self.btn_plus):
            b.setFixedHeight(32)
        self.btn_play.setStyleSheet('QPushButton{font-weight:bold;}')
        prow.addWidget(self.btn_play)
        prow.addWidget(self.btn_stop)
        prow.addWidget(self.btn_back)
        prow.addStretch(1)
        prow.addWidget(self.btn_minus)
        prow.addWidget(self.btn_plus)
        fp.addLayout(prow)

        self.sld_prog = QSlider(Qt.Orientation.Horizontal)
        self.sld_prog.setRange(0, 1000)
        fp.addWidget(self.sld_prog)

        self.lbl_time = QLabel('00:00.0 / 00:00.0')
        self.lbl_time.setStyleSheet('color:#9aa3b8;')
        fp.addWidget(self.lbl_time)
        box.addWidget(g_play)

        # ---------- 显示 ----------
        g_view = QGroupBox('显示')
        fv = QFormLayout(g_view)

        vrow = QHBoxLayout()
        self.btn_mode = QPushButton('切到下落式')
        self.btn_mode.setFixedHeight(30)
        self.lbl_mode = QLabel('当前：4×4 网格高亮式')
        vrow.addWidget(self.lbl_mode, 1)
        vrow.addWidget(self.btn_mode)
        fv.addRow('模式', vrow)

        self.sp_preview = QSpinBox()
        self.sp_preview.setRange(2, 12)
        self.sp_preview.setValue(5)
        self.sp_preview.setSuffix(' 个音')
        fv.addRow('往后预看', self.sp_preview)

        self.sld_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sld_opacity.setRange(25, 100)
        self.sld_opacity.setValue(92)
        fv.addRow('不透明度', self.sld_opacity)

        self.chk_labels = QCheckBox('格子上显示音名')
        self.chk_labels.setChecked(True)
        fv.addRow('', self.chk_labels)
        box.addWidget(g_view)

        # ---------- 悬浮窗 ----------
        g_pos = QGroupBox('谱面窗位置与大小')
        fg = QVBoxLayout(g_pos)

        grow = QHBoxLayout()
        self.sp_x = QSpinBox()
        self.sp_y = QSpinBox()
        self.sp_w = QSpinBox()
        self.sp_h = QSpinBox()
        self.sp_x.setRange(-4000, 8000)
        self.sp_y.setRange(-4000, 8000)
        self.sp_w.setRange(180, 3000)
        self.sp_h.setRange(180, 3000)
        self.sp_w.setValue(470)
        self.sp_h.setValue(580)
        for lbl, sp in (('X', self.sp_x), ('Y', self.sp_y),
                        ('宽', self.sp_w), ('高', self.sp_h)):
            grow.addWidget(QLabel(lbl))
            grow.addWidget(sp)
        fg.addLayout(grow)

        prow2 = QHBoxLayout()
        self.chk_drag = QCheckBox('允许拖动（临时取消鼠标穿透，拖完记得关掉）')
        btn_corner = QPushButton('贴到屏幕右上角')
        btn_corner.setFixedHeight(30)
        prow2.addWidget(self.chk_drag, 1)
        prow2.addWidget(btn_corner)
        fg.addLayout(prow2)

        self.lbl_tip = QLabel('提示：谱面窗平时是「鼠标穿透」的，枪能直接打过去；'
                              '要挪位置就先勾上左边这个开关。')
        self.lbl_tip.setWordWrap(True)
        self.lbl_tip.setStyleSheet('color:#9aa3b8;')
        fg.addWidget(self.lbl_tip)
        box.addWidget(g_pos)

        box.addStretch(1)

        self.btn_corner = btn_corner
        self.statusBar().showMessage('就绪')

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------

    def _wire(self):
        self.btn_open.clicked.connect(self._open_dialog)
        self.btn_edit.clicked.connect(self._edit_sheet)
        self.btn_reload.clicked.connect(self._reload)
        self.cmb_sheet.currentIndexChanged.connect(self._pick_from_combo)

        self.btn_play.clicked.connect(self.player.toggle)
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_back.clicked.connect(lambda: self.player.seek(0.0))
        self.btn_minus.clicked.connect(lambda: self.player.nudge(-0.5))
        self.btn_plus.clicked.connect(lambda: self.player.nudge(0.5))

        self.sld_prog.sliderReleased.connect(self._seek_from_slider)
        self.sld_prog.sliderPressed.connect(lambda: self.player.pause())

        self.btn_mode.clicked.connect(self._toggle_mode)
        self.sp_preview.valueChanged.connect(self._apply_view_options)
        self.sld_opacity.valueChanged.connect(
            lambda v: self.overlay.setWindowOpacity(v / 100.0))
        self.chk_labels.toggled.connect(self._apply_view_options)

        for sp in (self.sp_x, self.sp_y, self.sp_w, self.sp_h):
            sp.valueChanged.connect(self._apply_geometry)
        self.chk_drag.toggled.connect(self._on_drag_toggled)
        self.btn_corner.clicked.connect(self._snap_corner)

        self.player.tick.connect(self._on_tick)
        self.player.state_changed.connect(self._on_state)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.player.toggle)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  lambda: self.player.nudge(-0.5))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  lambda: self.player.nudge(0.5))
        QShortcut(QKeySequence(Qt.Key.Key_Home), self,
                  lambda: self.player.seek(0.0))

    # ------------------------------------------------------------------
    # 谱面
    # ------------------------------------------------------------------

    def _scan_sheets(self):
        self.cmb_sheet.blockSignals(True)
        self.cmb_sheet.clear()
        self.cmb_sheet.addItem('— 从仓库里挑一份 —', None)
        for path in all_sheets():
            self.cmb_sheet.addItem(os.path.basename(path), path)
        self.cmb_sheet.blockSignals(False)

    def _pick_from_combo(self, _idx):
        path = self.cmb_sheet.currentData()
        if path:
            self.load_sheet(path)

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '打开谱面', sheets_dir(), '谱面文件 (*.txt);;所有文件 (*)')
        if path:
            self.load_sheet(path)

    def _reload(self):
        if self.path:
            self.load_sheet(self.path)

    def _edit_sheet(self):
        dlg = EditorDialog(self.path, self)
        dlg.exec()
        if dlg.saved and dlg.path:
            self._scan_sheets()
            self.load_sheet(dlg.path)

    def load_sheet(self, path: str):
        try:
            sheet = parser.load(path)
        except Exception as e:
            QMessageBox.warning(self, '读取失败', str(e))
            return

        if not sheet:
            QMessageBox.warning(
                self, '这份谱子是空的',
                '没有解析出任何音符。\n\n'
                '检查一下是不是少了数字，或者用了不支持的符号。')
            return

        self.tl = timeline.Timeline(sheet)
        self.path = path
        self.player.set_timeline(self.tl)
        self.overlay.set_timeline(self.tl)

        bad = layout.unmapped_pitches(self.tl.all_pitches())
        txt = '<b>%s</b>　%s' % (sheet.title or os.path.basename(path),
                                 self.tl.stats())
        if bad:
            txt += ('<br><span style="color:#ff9a9a">琴上没有的音'
                    '（会跳过）：%s</span>' % ' '.join(bad))
        self.lbl_sheet.setText(txt)
        self.statusBar().showMessage('已载入 %s' % os.path.basename(path))
        self._save_config()

    # ------------------------------------------------------------------
    # 播放 / 显示
    # ------------------------------------------------------------------

    def _seek_from_slider(self):
        if not self.tl:
            return
        self.player.seek(self.sld_prog.value() / 1000.0 * self.tl.total_sec)

    def _on_tick(self, sec: float):
        if not self.tl:
            return
        total = max(0.001, self.tl.total_sec)
        if not self.sld_prog.isSliderDown():
            self.sld_prog.setValue(int(sec / total * 1000))
        idx = self.tl.index_at(sec)
        self.lbl_time.setText(
            '%s / %s　·　第 %d / %d 个音'
            % (_fmt_time(sec), _fmt_time(self.tl.total_sec),
               max(0, idx + 1), len(self.tl)))

    def _on_state(self, playing: bool):
        self.btn_play.setText('⏸  暂停' if playing else '▶  播放')

    def _toggle_mode(self):
        mode = self.overlay.toggle_mode()
        self.btn_mode.setText('切到下落式' if mode == 'grid' else '切到 4×4 网格')
        self.lbl_mode.setText('当前：%s'
                              % ('4×4 网格高亮式' if mode == 'grid' else '下落式'))

    def _apply_view_options(self):
        for v in (self.overlay.grid_view, self.overlay.fall_view):
            v.preview_count = self.sp_preview.value()
            v.show_labels = self.chk_labels.isChecked()
            v.update()

    # ------------------------------------------------------------------
    # 悬浮窗几何
    # ------------------------------------------------------------------

    def _apply_geometry(self):
        self.overlay.setGeometry(self.sp_x.value(), self.sp_y.value(),
                                 self.sp_w.value(), self.sp_h.value())
        self._save_config()

    def _on_drag_toggled(self, on: bool):
        self.overlay.set_click_through(not on)
        if on:
            self.overlay.show()
            self.overlay.raise_()
            self.statusBar().showMessage('现在可以拖动谱面窗了 —— 摆好后请取消勾选')
        else:
            self.statusBar().showMessage('已恢复鼠标穿透')
            self._sync_geom_from_overlay()

    def _sync_geom_from_overlay(self):
        g = self.overlay.geometry()
        for sp, val in ((self.sp_x, g.x()), (self.sp_y, g.y()),
                        (self.sp_w, g.width()), (self.sp_h, g.height())):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        self._save_config()

    def _snap_corner(self):
        scr = self.screen().availableGeometry() if self.screen() else None
        if not scr:
            return
        w = self.sp_w.value()
        self.sp_x.setValue(scr.right() - w - 20)
        self.sp_y.setValue(scr.top() + 20)
        self.statusBar().showMessage('已贴到右上角')

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _load_config(self):
        cfg = {}
        try:
            if os.path.isfile(config_path()):
                with open(config_path(), 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
        except Exception:
            cfg = {}

        for sp, key, dflt in ((self.sp_x, 'x', 3000), (self.sp_y, 'y', 60),
                              (self.sp_w, 'w', 470), (self.sp_h, 'h', 580)):
            sp.setValue(int(cfg.get(key, dflt)))
        self.sld_opacity.setValue(int(cfg.get('opacity', 92)))
        self.sp_preview.setValue(int(cfg.get('preview', 5)))
        self.overlay.setWindowOpacity(self.sld_opacity.value() / 100.0)
        self.overlay.set_mode(cfg.get('mode', 'grid'))
        self.lbl_mode.setText('当前：%s'
                              % ('4×4 网格高亮式'
                                 if self.overlay._mode == 'grid' else '下落式'))
        self.btn_mode.setText('切到下落式'
                              if self.overlay._mode == 'grid' else '切到 4×4 网格')

        last = cfg.get('last_sheet')
        if not (last and os.path.isfile(last)):
            demo = os.path.join(sheets_dir(), 'demo.txt')
            last = demo if os.path.isfile(demo) else None
        if last:
            self.load_sheet(last)
        self._apply_geometry()
        self._apply_view_options()

    def _save_config(self):
        cfg = {
            'x': self.sp_x.value(), 'y': self.sp_y.value(),
            'w': self.sp_w.value(), 'h': self.sp_h.value(),
            'opacity': self.sld_opacity.value(),
            'preview': self.sp_preview.value(),
            'mode': self.overlay._mode,
            'last_sheet': self.path,
        }
        try:
            with open(config_path(), 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._save_config()
        self.overlay.hide()
        self.overlay.close()
        event.accept()
        self.close()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
