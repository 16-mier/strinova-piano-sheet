# -*- coding: utf-8 -*-
"""主控制窗 —— 选谱、播放、切换显示、摆悬浮窗的位置。"""

from __future__ import annotations

import json
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import layout, parser, recorder, timeline, transcribe
from core.paths import APP_NAME, all_sheets, config_path, sheets_dir

from .editor import EditorDialog
from .hotkeys import (HK_LISTEN, HK_PLAYPAUSE, HK_RESTART, HOTKEY_LABELS,
                      HotkeyManager, choice_index_by_name)
from .listen_dialog import ListenDialog
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

        # 听音记谱相关
        self._cfg: dict = {}
        self._rec: recorder.LoopbackRecorder | None = None
        self._listen: ListenDialog | None = None
        self._listen_result = ''
        self._tick_listen = QTimer(self)
        self._tick_listen.setInterval(300)
        self._tick_listen.timeout.connect(self._update_listen_toast)

        # 全局热键挂在谱面窗上 —— 它常驻、置顶、且不会抢焦点
        self.hotkeys = HotkeyManager(overlay, self)
        overlay.hotkeys = self.hotkeys
        self.hotkeys.fired.connect(self._on_hotkey)

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
        btn_listen = QPushButton('🎧 听音记谱')
        btn_listen.setToolTip('把游戏里弹的琴录下来，自动变成谱子')
        for b in (btn_open, btn_edit, btn_reload, btn_listen):
            b.setFixedHeight(30)
        row.addWidget(QLabel('曲谱仓库'))
        row.addWidget(self.cmb_sheet, 1)
        row.addWidget(btn_open)
        row.addWidget(btn_edit)
        row.addWidget(btn_reload)
        row.addWidget(btn_listen)
        f.addLayout(row)

        self.lbl_sheet = QLabel('还没有载入谱面')
        self.lbl_sheet.setWordWrap(True)
        self.lbl_sheet.setStyleSheet('color:#9aa3b8;')
        f.addWidget(self.lbl_sheet)

        self.btn_open, self.btn_edit, self.btn_reload = (btn_open, btn_edit,
                                                        btn_reload)
        self.btn_listen = btn_listen
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

        # ---------- 游戏内快捷键 ----------
        g_hk = QGroupBox('游戏内快捷键（按下不会抢游戏窗口）')
        fh = QFormLayout(g_hk)

        self.cmb_hk_play = QComboBox()
        self.cmb_hk_play.addItems(HOTKEY_LABELS)
        self.cmb_hk_restart = QComboBox()
        self.cmb_hk_restart.addItems(HOTKEY_LABELS)
        self.cmb_hk_listen = QComboBox()
        self.cmb_hk_listen.addItems(HOTKEY_LABELS)
        fh.addRow('开始 / 暂停', self.cmb_hk_play)
        fh.addRow('从头开始', self.cmb_hk_restart)
        fh.addRow('听音记谱 开/停', self.cmb_hk_listen)

        self.sp_count = QSpinBox()
        self.sp_count.setRange(0, 15)
        self.sp_count.setValue(3)
        self.sp_count.setSuffix(' 秒')
        self.sp_count.setToolTip(
            '按开始后先倒数这么多秒，让你把注意力挪回游戏')
        fh.addRow('按下后倒计时', self.sp_count)

        self.lbl_hk = QLabel(
            '这些键走的是系统级注册：按下时游戏不会失焦、鼠标锁定也不会被解除。'
            '代价是这个键游戏就收不到了 —— 所以别绑 WASD / 空格 / V / U 这些'
            '游戏要用的键，用 F 系列或小键盘最稳。')
        self.lbl_hk.setWordWrap(True)
        self.lbl_hk.setStyleSheet('color:#9aa3b8;')
        fh.addRow('', self.lbl_hk)
        box.addWidget(g_hk)

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
        self.btn_listen.clicked.connect(self._open_listen)
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

        self.cmb_hk_play.currentIndexChanged.connect(
            lambda _i: self._apply_hotkeys())
        self.cmb_hk_restart.currentIndexChanged.connect(
            lambda _i: self._apply_hotkeys())
        self.cmb_hk_listen.currentIndexChanged.connect(
            lambda _i: self._apply_hotkeys())
        self.sp_count.valueChanged.connect(lambda _v: self._save_config())

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

    # ------------------------------------------------------------------
    # 游戏内快捷键
    # ------------------------------------------------------------------

    def _apply_hotkeys(self):
        """按下拉框的选择重新注册全局热键。"""
        i_play = self.cmb_hk_play.currentIndex()
        i_rest = self.cmb_hk_restart.currentIndex()
        i_listen = self.cmb_hk_listen.currentIndex()
        ok_play = self.hotkeys.bind(HK_PLAYPAUSE, i_play)
        ok_rest = self.hotkeys.bind(HK_RESTART, i_rest)
        ok_listen = self.hotkeys.bind(HK_LISTEN, i_listen)

        msgs = []
        if i_play and not ok_play:
            msgs.append('「开始/暂停」注册失败：%s'
                        % self.hotkeys.last_error(HK_PLAYPAUSE))
        if i_rest and not ok_rest:
            msgs.append('「从头开始」注册失败：%s'
                        % self.hotkeys.last_error(HK_RESTART))
        if i_listen and not ok_listen:
            msgs.append('「听音记谱」注册失败：%s'
                        % self.hotkeys.last_error(HK_LISTEN))
        if msgs:
            self.statusBar().showMessage('；'.join(msgs))
        self._save_config()

    def _on_hotkey(self, hk_id: int):
        if hk_id == HK_PLAYPAUSE:
            self._hotkey_playpause()
        elif hk_id == HK_RESTART:
            self._hotkey_restart()
        elif hk_id == HK_LISTEN:
            self._toggle_listen()

    def _hotkey_playpause(self):
        """开始 / 暂停。开始前先走倒计时，好让你把注意力挪回游戏。"""
        if self.overlay.counting:              # 倒计时中再按 = 取消
            self.overlay.cancel_countdown()
            self.statusBar().showMessage('已取消倒计时')
            return
        if self.player.playing:
            self.player.pause()
            self.statusBar().showMessage('已暂停')
            return
        secs = self.sp_count.value()
        if secs:
            self.overlay.start_countdown(secs, self._begin_play)
            self.statusBar().showMessage('倒计时 %d 秒…' % secs)
        else:
            self._begin_play()

    def _hotkey_restart(self):
        """从头开始：先停下，倒数若干秒，再从第一拍开播。"""
        self.player.stop()
        secs = self.sp_count.value()
        if secs:
            self.overlay.start_countdown(
                secs, lambda: self._begin_play(reset=True))
            self.statusBar().showMessage('从头开始，倒计时 %d 秒…' % secs)
        else:
            self._begin_play(reset=True)

    def _begin_play(self, reset: bool = False):
        if not (self.tl and self.tl.items):
            self.statusBar().showMessage('还没有载入谱面')
            return
        if reset:
            self.player.seek(0.0)
        self.player.play()
        self.statusBar().showMessage('谱面开始')

    # ------------------------------------------------------------------
    # 听音记谱
    # ------------------------------------------------------------------

    def _cur_bpm(self) -> int:
        if self.tl and self.tl.bpm_points:
            return int(self.tl.bpm_points[0][1])
        return 120

    def _listen_device_id(self) -> str | None:
        """该录哪个设备（返回设备 id）。"""
        if self._listen is not None and self._listen.cmb_dev.count():
            got = self._listen.cmb_dev.currentData()
            if got:
                return str(got)
        kw = self._cfg.get('listen_device', '')
        if kw:
            got = recorder.find_device_id(kw)
            if got:
                return got
        return recorder.guess_game_device()

    def _open_listen(self):
        """打开听音记谱窗口（手动模式）。"""
        if self._listen is None:
            self._listen = ListenDialog(
                self, bpm=self._cur_bpm(),
                device_keyword=self._cfg.get('listen_device', ''),
                insert_cb=self._insert_listen_result)
        if self._listen_result and not self._listen.text.toPlainText().strip():
            self._listen.apply_text(self._listen_result)
        self._listen.sp_bpm.setValue(self._cur_bpm())
        self._listen.show()
        self._listen.raise_()
        self._listen.activateWindow()

    def _toggle_listen(self):
        """热键：开始/结束录音 —— 全程不弹窗、不抢游戏焦点。"""
        if self._rec is None:
            self._rec = recorder.LoopbackRecorder()

        if self._rec.recording:
            data = self._rec.stop()
            self._tick_listen.stop()
            secs = len(data) / max(1, self._rec.samplerate)
            if secs < 0.5:
                self.overlay.show_toast('录得太短了（%.1f 秒）' % secs)
                return
            self.overlay.show_toast('录到 %.1f 秒，正在识别…' % secs, 60)
            QApplication.processEvents()
            self._transcribe_recorded(data)
            return

        dev = self._listen_device_id()
        if not dev:
            self.overlay.show_toast('没找到录音设备\n'
                                    '先去控制台的「听音记谱」里选一个')
            return
        ok = self._rec.start(str(dev), channels=2)
        if not ok:
            self._rec.abort()
            ok = self._rec.start(str(dev), channels=1)
        if not ok:
            self.overlay.show_toast('开不了录音：\n%s'
                                    % (self._rec.last_error or '')[:80])
            return
        self._tick_listen.start()
        self.overlay.show_toast('● 录音中… 再按一次结束', 1.6)

    def _update_listen_toast(self):
        if self._rec and self._rec.recording:
            self.overlay.show_toast('● 录音中 %.1f 秒\n再按一次结束'
                                    % self._rec.seconds, 0.8)

    def _transcribe_recorded(self, data):
        rate = self._rec.samplerate if self._rec else 48000
        bpm = self._cur_bpm()
        _tokens, hits = transcribe.transcribe(data, rate, bpm=bpm)
        if not hits:
            self.overlay.show_toast('没识别出音符 😕\n'
                                    '可能录到的是静音，或者设备选错了', 3.5)
            return
        text, _ = transcribe.to_sheet_text(data, rate, bpm=bpm,
                                           title='听音记谱')
        self._listen_result = text
        self.statusBar().showMessage(
            '听音记谱完成：%d 个音，点「🎧 听音记谱」查看' % len(hits))
        self.overlay.show_toast('认出来了：%d 个音 ✅\n点控制台的'
                                '「听音记谱」查看' % len(hits), 3.5)

    def _insert_listen_result(self, text: str):
        self._listen_result = text
        self.statusBar().showMessage('识别结果已就绪')

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
        self._cfg = cfg

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

        self.sp_count.setValue(int(cfg.get('countdown', 3)))
        self.cmb_hk_play.setCurrentIndex(
            choice_index_by_name(cfg.get('hk_play', '不绑定')))
        self.cmb_hk_restart.setCurrentIndex(
            choice_index_by_name(cfg.get('hk_restart', '不绑定')))
        self.cmb_hk_listen.setCurrentIndex(
            choice_index_by_name(cfg.get('hk_listen', '不绑定')))

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
            'hk_play': self.cmb_hk_play.currentText(),
            'hk_restart': self.cmb_hk_restart.currentText(),
            'hk_listen': self.cmb_hk_listen.currentText(),
            'countdown': self.sp_count.value(),
            'listen_device': (self._listen.cmb_dev.currentText()
                              if self._listen is not None else
                              self._cfg.get('listen_device', '')),
        }
        try:
            with open(config_path(), 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        # 窗口真正显示之后句柄才有效，这时再注册全局热键
        QTimer.singleShot(300, self._apply_hotkeys)

    def closeEvent(self, event):
        try:
            self.hotkeys.unbind_all()
        except Exception:
            pass
        self._save_config()
        self.overlay.hide()
        self.overlay.close()
        event.accept()
        self.close()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
