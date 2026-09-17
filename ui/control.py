# -*- coding: utf-8 -*-
"""主控制窗 —— 选谱、播放、切换显示、摆悬浮窗的位置。"""

from __future__ import annotations

import json
import os

import numpy as np

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import (broadcast, layout, live, parser, recorder, timeline,
                  transcribe, winfocus)
from core.paths import APP_NAME, all_sheets, config_path, sheets_dir

from .editor import EditorDialog
from .hotkeys import (HK_LISTEN, HK_PLAYPAUSE, HK_RESTART, HK_TOGGLE,
                      HotkeyEdit, HotkeyManager, parse_binding)
from .listen_dialog import ListenDialog
from .overlay import OverlayWindow, Player


def _fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    return '%02d:%04.1f' % (m, sec - m * 60)


# 贴到游戏窗口的锚点：(显示名, 横向比例, 纵向比例)
ANCHORS = [('左上', 0.0, 0.0), ('上中', 0.5, 0.0), ('右上', 1.0, 0.0),
           ('左中', 0.0, 0.5), ('正中', 0.5, 0.5), ('右中', 1.0, 0.5),
           ('左下', 0.0, 1.0), ('下中', 0.5, 1.0), ('右下', 1.0, 1.0)]

AUDIO_FILTER = ('音频 / 视频 (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus '
                '*.wma *.mp4 *.mkv *.webm *.flv *.mov *.avi *.ts);;'
                '所有文件 (*)')


class _WorkerBridge(QObject):
    """后台线程 → 主线程 的回传桥（Qt 控件只能在主线程碰）。"""

    done = pyqtSignal(object)      # 结果 dict
    failed = pyqtSignal(str)
    note = pyqtSignal(str, float)  # 实时跟弹：音高, 实测频率


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

        # 前台窗口轮询 —— 让浮窗「只在卡丘窗口在前面时才露面」
        self._fg_last = None
        self._tick_fg = QTimer(self)
        self._tick_fg.setInterval(500)
        self._tick_fg.timeout.connect(self._poll_foreground)

        # 绑在游戏窗口上：记的是「相对游戏窗口的比例」，窗口一动浮窗就跟着挪
        self._pin_x = 0.0
        self._pin_y = 0.0
        self._last_game_rect = None
        self._tick_pin = QTimer(self)
        self._tick_pin.setInterval(400)
        self._tick_pin.timeout.connect(self._follow_tick)

        # 播音：把音频送到虚拟声卡（游戏麦克风就收到了）
        self._caster = broadcast.Broadcaster()
        self._tick_cast = QTimer(self)
        self._tick_cast.setInterval(200)
        self._tick_cast.timeout.connect(self._update_cast)

        # 导入音频生成谱面（在后台线程跑，别卡界面）
        self._importing = False
        self._bridge = _WorkerBridge(self)
        self._bridge.done.connect(self._on_import_done)
        self._bridge.failed.connect(self._on_import_failed)
        self._bridge.note.connect(self._on_live_note)

        # 实时跟弹 / 录音（共用一条 loopback 流）
        self._stream: recorder.LoopbackStream | None = None
        self._live: live.LiveDetector | None = None
        self._live_only = False
        self._recording = False
        self._rec_chunks: list = []
        self._live_count = 0

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
        btn_import = QPushButton('📂 导入音频')
        btn_import.setToolTip(
            '选一个音频或视频（B站下下来的 mp4 直接丢进来也行），\n'
            '自动听出里面的琴声，变成可以编辑的谱子')
        btn_live = QPushButton('👀 实时跟弹')
        btn_live.setToolTip(
            '不录音，只在浮窗上实时显示游戏里敲的是哪个键。\n'
            '找手感、核对键位用它最方便（按全局热键也能开关）')
        for b in (btn_open, btn_edit, btn_reload, btn_listen, btn_import,
                  btn_live):
            b.setFixedHeight(30)
        row.addWidget(QLabel('曲谱仓库'))
        row.addWidget(self.cmb_sheet, 1)
        row.addWidget(btn_open)
        row.addWidget(btn_edit)
        row.addWidget(btn_reload)
        row.addWidget(btn_listen)
        row.addWidget(btn_import)
        row.addWidget(btn_live)
        f.addLayout(row)

        self.lbl_sheet = QLabel('还没有载入谱面')
        self.lbl_sheet.setWordWrap(True)
        self.lbl_sheet.setStyleSheet('color:#9aa3b8;')
        f.addWidget(self.lbl_sheet)

        self.lbl_live = QLabel('实时跟弹：没在听')
        self.lbl_live.setStyleSheet('color:#7fd8c0;')
        f.addWidget(self.lbl_live)

        self.btn_open, self.btn_edit, self.btn_reload = (btn_open, btn_edit,
                                                        btn_reload)
        self.btn_listen = btn_listen
        self.btn_import = btn_import
        self.btn_live = btn_live
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

        # ---------- 播音到游戏麦克风 ----------
        g_cast = QGroupBox('🎙 播音到游戏麦克风（把曲子放给队友听）')
        fc = QVBoxLayout(g_cast)

        crow = QHBoxLayout()
        self.txt_cast = QLineEdit()
        self.txt_cast.setPlaceholderText('选一个音频或视频文件…')
        self.btn_cast_pick = QPushButton('选文件…')
        self.btn_cast_play = QPushButton('▶  播出')
        self.btn_cast_stop = QPushButton('⏹  停止')
        for b in (self.btn_cast_pick, self.btn_cast_play, self.btn_cast_stop):
            b.setFixedHeight(30)
        crow.addWidget(QLabel('音源'))
        crow.addWidget(self.txt_cast, 1)
        crow.addWidget(self.btn_cast_pick)
        crow.addWidget(self.btn_cast_play)
        crow.addWidget(self.btn_cast_stop)
        fc.addLayout(crow)

        drow = QHBoxLayout()
        self.cmb_cast_out = QComboBox()
        self.cmb_cast_out.setMinimumWidth(220)
        self.btn_cast_test = QPushButton('试听')
        self.btn_cast_refresh = QPushButton('刷新设备')
        drow.addWidget(QLabel('送进'))
        drow.addWidget(self.cmb_cast_out, 1)
        drow.addWidget(self.btn_cast_test)
        drow.addWidget(self.btn_cast_refresh)
        fc.addLayout(drow)

        mrow = QHBoxLayout()
        self.chk_cast_mon = QCheckBox('自己也监听')
        self.cmb_cast_mon = QComboBox()
        self.cmb_cast_mon.setMinimumWidth(220)
        mrow.addWidget(self.chk_cast_mon)
        mrow.addWidget(self.cmb_cast_mon, 1)
        fc.addLayout(mrow)

        vrow2 = QHBoxLayout()
        self.sld_cast_vol = QSlider(Qt.Orientation.Horizontal)
        self.sld_cast_vol.setRange(0, 150)
        self.sld_cast_vol.setValue(100)
        self.sld_cast_vol.setToolTip('100% 就是原音量；超过 100% 会削顶，'
                                     '除非对面嫌小，否则别拉太高')
        self.lbl_cast_vol = QLabel('100%')
        self.lbl_cast_vol.setFixedWidth(46)
        self.lbl_cast_pos = QLabel('00:00.0 / 00:00.0')
        self.lbl_cast_pos.setStyleSheet('color:#9aa3b8;')
        vrow2.addWidget(QLabel('音量'))
        vrow2.addWidget(self.sld_cast_vol, 1)
        vrow2.addWidget(self.lbl_cast_vol)
        vrow2.addSpacing(12)
        vrow2.addWidget(self.lbl_cast_pos)
        fc.addLayout(vrow2)

        lbl_cast_tip = QLabel(
            '「送进」要挑带【虚拟】的那个（Voicemeeter Input / CABLE 之类），'
            '游戏里的麦克风再选成对应的那一路 —— 队友就听得到了。\n'
            '勾上「自己也监听」再挑个耳机，你就能同步听到。'
            '播音全程不碰游戏窗口，不会让它失焦。')
        lbl_cast_tip.setWordWrap(True)
        lbl_cast_tip.setStyleSheet('color:#9aa3b8;')
        fc.addWidget(lbl_cast_tip)
        box.addWidget(g_cast)

        # ---------- 显示 ----------
        g_view = QGroupBox('显示')
        fv = QFormLayout(g_view)

        self.sp_preview = QSpinBox()
        self.sp_preview.setRange(2, 12)
        self.sp_preview.setValue(5)
        self.sp_preview.setSuffix(' 个音')
        fv.addRow('往后预看', self.sp_preview)

        orow = QHBoxLayout()
        self.sld_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sld_opacity.setRange(10, 100)
        self.sld_opacity.setValue(92)
        self.sld_opacity.setToolTip('整个浮窗的透明度（连音名文字一起变淡）')
        self.lbl_opacity = QLabel('92%')
        self.lbl_opacity.setFixedWidth(42)
        orow.addWidget(self.sld_opacity, 1)
        orow.addWidget(self.lbl_opacity)
        fv.addRow('整体不透明度', orow)

        brow = QHBoxLayout()
        self.sld_bg = QSlider(Qt.Orientation.Horizontal)
        self.sld_bg.setRange(0, 100)
        self.sld_bg.setValue(100)
        self.sld_bg.setToolTip(
            '只调底板 / 格子的浓度，音名文字不受影响。\n'
            '想「几乎看不见框、只留字和高亮」就把它拉低；拉到 0 就只剩字。')
        self.lbl_bg = QLabel('100%')
        self.lbl_bg.setFixedWidth(42)
        brow.addWidget(self.sld_bg, 1)
        brow.addWidget(self.lbl_bg)
        fv.addRow('底板浓度', brow)

        self.chk_labels = QCheckBox('格子上显示音名')
        self.chk_labels.setChecked(True)
        fv.addRow('', self.chk_labels)

        self.chk_overlay = QCheckBox('显示谱面浮窗')
        self.chk_overlay.setChecked(True)
        self.chk_overlay.setToolTip(
            '关掉之后浮窗彻底不露面（全局热键照样在）。\n'
            '绑了「浮窗 显示/隐藏」热键的话，游戏里也能一键切。')
        fv.addRow('', self.chk_overlay)

        self.chk_only_game = QCheckBox('只在卡拉彼丘窗口在前台时才显示')
        self.chk_only_game.setChecked(True)
        self.chk_only_game.setToolTip(
            '勾上之后：切出去看网页 / 打字时浮窗自动隐身，回到游戏立刻现形。\n'
            '隐身 ≠ 关闭 —— 全局热键在隐身状态下照样能按。')
        fv.addRow('', self.chk_only_game)

        self.lbl_fg = QLabel('—')
        self.lbl_fg.setStyleSheet('color:#7f8aa3;')
        fv.addRow('前台窗口', self.lbl_fg)
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
        self.chk_pin = QCheckBox(
            '绑在卡拉彼丘窗口上（游戏窗口一动 / 改分辨率，浮窗跟着走）')
        self.chk_pin.setToolTip(
            '记的是「相对游戏窗口的比例」。\n'
            '游戏窗口挪位、换分辨率之后，浮窗会按同样比例自动挪过去。')
        prow2.addWidget(self.chk_pin, 1)
        fg.addLayout(prow2)

        arow = QHBoxLayout()
        arow.addWidget(QLabel('贴到游戏窗口'))
        for label, ax, ay in ANCHORS:
            b = QPushButton(label)
            b.setFixedSize(46, 26)
            b.setToolTip('贴到游戏窗口的「%s」' % label)
            b.clicked.connect(lambda _=False, a=ax, c=ay: self._snap_to(a, c))
            arow.addWidget(b)
        arow.addStretch(1)
        fg.addLayout(arow)

        prow3 = QHBoxLayout()
        self.chk_drag = QCheckBox('允许拖动（临时取消鼠标穿透）')
        self.btn_remember = QPushButton('记住当前位置')
        self.btn_remember.setFixedHeight(30)
        self.btn_remember.setToolTip('把浮窗现在的位置记成绑定比例（拖好了再点）')
        btn_corner = QPushButton('贴到屏幕右上角')
        btn_corner.setFixedHeight(30)
        prow3.addWidget(self.chk_drag, 1)
        prow3.addWidget(self.btn_remember)
        prow3.addWidget(btn_corner)
        fg.addLayout(prow3)

        self.lbl_tip = QLabel('提示：谱面窗平时是「鼠标穿透」的，枪能直接打过去；'
                              '要挪位置就先勾上左边这个开关。'
                              '游戏里画面一直在动，用上面那排按钮贴位置最省事。')
        self.lbl_tip.setWordWrap(True)
        self.lbl_tip.setStyleSheet('color:#9aa3b8;')
        fg.addWidget(self.lbl_tip)
        box.addWidget(g_pos)

        # ---------- 游戏内快捷键 ----------
        g_hk = QGroupBox('游戏内快捷键（按下不会抢游戏窗口）')
        fh = QFormLayout(g_hk)

        self.hk_play = HotkeyEdit()
        self.hk_restart = HotkeyEdit()
        self.hk_listen = HotkeyEdit()
        self.hk_toggle = HotkeyEdit()
        fh.addRow('开始 / 暂停', self.hk_play)
        fh.addRow('从头开始', self.hk_restart)
        fh.addRow('听音记谱 开/停', self.hk_listen)
        fh.addRow('浮窗 显示/隐藏', self.hk_toggle)

        self.sp_count = QSpinBox()
        self.sp_count.setRange(0, 15)
        self.sp_count.setValue(3)
        self.sp_count.setSuffix(' 秒')
        self.sp_count.setToolTip(
            '按开始后先倒数这么多秒，让你把注意力挪回游戏')
        fh.addRow('按下后倒计时', self.sp_count)

        self.lbl_hk = QLabel(
            '点一下右边的框，然后直接按下你想用的键就绑好了'
            '（录制中 Esc = 取消，Delete = 清除）。绑到游戏要用的键会变红提醒。\n'
            '这些键走系统级注册：按下时游戏不会失焦、鼠标锁定也不会被解除；'
            '代价是这个键游戏就收不到了 —— 所以用 F 系列或小键盘最稳。')
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
        self.btn_import.clicked.connect(self._pick_import_file)
        self.btn_live.clicked.connect(self._toggle_live)
        self.cmb_sheet.currentIndexChanged.connect(self._pick_from_combo)

        self.btn_play.clicked.connect(self.player.toggle)
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_back.clicked.connect(lambda: self.player.seek(0.0))
        self.btn_minus.clicked.connect(lambda: self.player.nudge(-0.5))
        self.btn_plus.clicked.connect(lambda: self.player.nudge(0.5))

        self.sld_prog.sliderReleased.connect(self._seek_from_slider)
        self.sld_prog.sliderPressed.connect(lambda: self.player.pause())

        self.btn_cast_pick.clicked.connect(self._pick_cast_file)
        self.btn_cast_play.clicked.connect(self._cast_play)
        self.btn_cast_stop.clicked.connect(self._cast_stop)
        self.btn_cast_test.clicked.connect(self._cast_test)
        self.btn_cast_refresh.clicked.connect(self._refresh_cast_devices)
        self.sld_cast_vol.valueChanged.connect(
            lambda v: self.lbl_cast_vol.setText('%d%%' % v))
        for w in (self.sld_cast_vol, self.chk_cast_mon, self.cmb_cast_out,
                  self.cmb_cast_mon):
            sig = (w.valueChanged if isinstance(w, QSlider)
                   else w.toggled if isinstance(w, QCheckBox)
                   else w.currentIndexChanged)
            sig.connect(lambda *_a: self._save_config())

        self.sp_preview.valueChanged.connect(self._apply_view_options)
        self.sld_opacity.valueChanged.connect(self._on_opacity_changed)
        self.sld_bg.valueChanged.connect(self._on_bg_changed)
        self.chk_labels.toggled.connect(self._apply_view_options)

        for sp in (self.sp_x, self.sp_y, self.sp_w, self.sp_h):
            sp.valueChanged.connect(self._apply_geometry)
        self.chk_drag.toggled.connect(self._on_drag_toggled)
        self.chk_pin.toggled.connect(self._on_pin_toggled)
        self.btn_corner.clicked.connect(self._snap_corner)
        self.btn_remember.clicked.connect(self._remember_position)

        self.player.tick.connect(self._on_tick)
        self.player.state_changed.connect(self._on_state)

        for ed in (self.hk_play, self.hk_restart, self.hk_listen,
                   self.hk_toggle):
            ed.changed.connect(self._apply_hotkeys)
            ed.recording_changed.connect(self._on_hk_recording)
        self.sp_count.valueChanged.connect(lambda _v: self._save_config())

        self.chk_overlay.toggled.connect(self._on_overlay_show_toggled)
        self.chk_only_game.toggled.connect(self._on_only_game_toggled)

        # 面板里的快捷键（录制热键时要临时禁掉，否则按空格会被它截走）
        self._shortcuts = [
            QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.player.toggle),
            QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                      lambda: self.player.nudge(-0.5)),
            QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                      lambda: self.player.nudge(0.5)),
            QShortcut(QKeySequence(Qt.Key.Key_Home), self,
                      lambda: self.player.seek(0.0)),
        ]

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
        """按四个绑定框当前的值重新注册全局热键。"""
        pairs = ((HK_PLAYPAUSE, self.hk_play, '开始/暂停'),
                 (HK_RESTART, self.hk_restart, '从头开始'),
                 (HK_LISTEN, self.hk_listen, '听音记谱'),
                 (HK_TOGGLE, self.hk_toggle, '浮窗显示/隐藏'))
        msgs = []
        for hk_id, ed, name in pairs:
            if ed.vk and not self.hotkeys.bind(hk_id, ed.mods, ed.vk):
                msgs.append('「%s」注册失败：%s'
                            % (name, self.hotkeys.last_error(hk_id)))
            elif not ed.vk:
                self.hotkeys.bind(hk_id, 0, 0)
        if msgs:
            self.statusBar().showMessage('；'.join(msgs))
        else:
            used = [ed.text() for _i, ed, _n in pairs if ed.vk]
            self.statusBar().showMessage(
                ('游戏内快捷键：' + '　'.join(used)) if used
                else '游戏内快捷键：都还没绑')
        self._save_config()

    def _on_hk_recording(self, on: bool):
        """录制热键时把面板自己的快捷键让开，不然按空格会被它吃掉。"""
        for sc in self._shortcuts:
            sc.setEnabled(not on)

    def _on_hotkey(self, hk_id: int):
        if hk_id == HK_PLAYPAUSE:
            self._hotkey_playpause()
        elif hk_id == HK_RESTART:
            self._hotkey_restart()
        elif hk_id == HK_LISTEN:
            self._toggle_listen()
        elif hk_id == HK_TOGGLE:
            self._hotkey_toggle_overlay()

    def _hotkey_toggle_overlay(self):
        """热键：浮窗显示 / 隐藏 一键切。"""
        self.chk_overlay.setChecked(not self.chk_overlay.isChecked())

    # ------------------------------------------------------------------
    # 浮窗露不露面
    # ------------------------------------------------------------------

    def _on_overlay_show_toggled(self, on: bool):
        self._apply_overlay_visibility()
        self.statusBar().showMessage(
            '谱面浮窗：%s' % ('显示' if on else '已隐藏（按热键可唤回）'))
        self._save_config()

    def _on_only_game_toggled(self, _on: bool):
        self._apply_overlay_visibility()
        self._save_config()

    def _apply_overlay_visibility(self):
        """综合两个开关，决定浮窗「露脸 / 隐身 / 彻底关掉」。

        隐身用的是 windowOpacity = 0，而不是 hide()：隐藏掉的窗口收不到
        WM_HOTKEY，一隐身就再也按热键唤不回来了。
        """
        if not self.chk_overlay.isChecked():
            self._tick_fg.stop()
            self.overlay.set_ghost(False)
            self.overlay.hide()
            return
        if not self.overlay.isVisible():
            self.overlay.show()      # OverlayWindow 有「绝不抢焦点」的三重保险
        if self.chk_only_game.isChecked():
            in_game = winfocus.game_in_foreground()
            self._fg_last = in_game
            self.overlay.set_ghost(not in_game)
            self._tick_fg.start()
        else:
            self._tick_fg.stop()
            self._fg_last = None
            self.overlay.set_ghost(False)

    def _poll_foreground(self):
        """每半秒看一眼前台是不是卡丘；变了才动手，不做无谓的切换。"""
        if not (self.chk_overlay.isChecked()
                and self.chk_only_game.isChecked()):
            self._tick_fg.stop()
            return
        in_game = winfocus.game_in_foreground()
        if in_game != self._fg_last:
            self._fg_last = in_game
            self.overlay.set_ghost(not in_game)
        txt = '%s　%s' % ('🎮 卡丘在前台' if in_game else '…不是卡丘',
                          winfocus.describe_foreground())
        if txt != self.lbl_fg.text():
            self.lbl_fg.setText(txt)

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

    # ------------------------------------------------------------------
    # 实时跟弹 / 录音（共用同一条 loopback 流）
    # ------------------------------------------------------------------

    def _start_audio(self, record: bool) -> bool:
        """开一条 loopback 流。record=True 攒着回头转录，False 就只是听着。"""
        dev = self._listen_device_id()
        if not dev:
            self.overlay.show_toast('没找到录音设备\n'
                                    '先去控制台的「听音记谱」里选一个')
            return False
        self._live = live.LiveDetector(rate=48000)
        self._live_only = not record
        self._recording = record
        self._rec_chunks = []
        self._live_count = 0
        self._stream = recorder.LoopbackStream(self._on_audio_block,
                                               blocksize=1024,
                                               samplerate=48000)
        ok = self._stream.start(str(dev), channels=2)
        if not ok:
            self._stream.stop()
            ok = self._stream.start(str(dev), channels=1)
        if not ok:
            err = self._stream.last_error
            self._stream = None
            self._live = None
            self._recording = False
            self.overlay.show_toast('开不了监听：\n%s' % (err or '')[:80])
            return False
        self.overlay.clear_flash()
        return True

    def _stop_audio(self):
        if self._stream is not None:
            self._stream.stop()
        self._stream = None
        self._live = None
        self._live_only = False
        self.btn_live.setText('👀 实时跟弹')
        self.overlay.clear_flash()
        self.lbl_live.setText('实时跟弹：没在听')

    def _on_audio_block(self, block):
        """⚠ 这个跑在录音线程里 —— 只能碰纯数据，绝不许碰界面。"""
        if self._recording:
            self._rec_chunks.append(np.asarray(block, dtype=np.float32))
        det = self._live
        if det is None:
            return
        for _t, pitch, freq in det.push(block):
            self._bridge.note.emit(pitch, float(freq))

    def _on_live_note(self, pitch: str, freq: float):
        """回到主线程了 —— 亮浮窗、更新状态行。"""
        self.overlay.flash_note(pitch)
        self._live_count += 1
        self.lbl_live.setText('刚听到：%s（%.0f Hz）　本次累计 %d 个'
                              % (pitch, freq, self._live_count))

    def _toggle_live(self):
        """只听不录 —— 游戏里敲哪个键，浮窗就亮哪个。"""
        if self._stream is not None and self._stream.running:
            was_rec = self._recording
            self._recording = False
            self._stop_audio()
            self._tick_listen.stop()
            if was_rec:
                self._finish_recording()
            self.statusBar().showMessage('已停止监听')
            return
        if not self._start_audio(record=False):
            return
        self.btn_live.setText('⏹ 停止跟弹')
        self.lbl_live.setText('实时跟弹：正在听…去游戏里敲几下')
        self.statusBar().showMessage('实时跟弹中 —— 浮窗上会亮出你敲的键')

    def _toggle_listen(self):
        """热键：开始/结束录音 —— 全程不弹窗、不抢游戏焦点。

        录音期间浮窗同样会实时亮键，弹完还能直接看见自己弹了啥。
        """
        if self._recording:
            self._recording = False
            self._stop_audio()
            self._tick_listen.stop()
            self._finish_recording()
            return
        if self._stream is not None and self._stream.running:
            self._stop_audio()
        if not self._start_audio(record=True):
            return
        self.btn_live.setText('⏹ 停止录音')
        self.lbl_live.setText('录音中…浮窗会实时亮出你敲的键')
        self._tick_listen.start()
        self.overlay.show_toast('● 录音中… 再按一次结束', 1.6)

    def _finish_recording(self):
        chunks = self._rec_chunks
        self._rec_chunks = []
        data = (np.concatenate(chunks) if chunks
                else np.zeros(0, dtype=np.float32))
        secs = len(data) / 48000.0
        if secs < 0.5:
            self.overlay.show_toast('录得太短了（%.1f 秒）' % secs)
            return
        self.overlay.show_toast('录到 %.1f 秒，正在识别…' % secs, 60)
        QApplication.processEvents()
        self._transcribe_recorded(data)

    def _update_listen_toast(self):
        if self._recording:
            secs = len(self._rec_chunks) * 1024 / 48000.0
            self.overlay.show_toast('● 录音中 %.1f 秒\n再按一次结束'
                                    % secs, 0.8)

    def _transcribe_recorded(self, data):
        rate = 48000
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

    def _on_opacity_changed(self, v: int):
        self.lbl_opacity.setText('%d%%' % v)
        self.overlay.set_user_opacity(v / 100.0)
        self._save_config()

    def _on_bg_changed(self, v: int):
        self.lbl_bg.setText('%d%%' % v)
        self._apply_view_options()
        self._save_config()

    def _apply_view_options(self):
        v = self.overlay.grid_view
        v.preview_count = self.sp_preview.value()
        v.show_labels = self.chk_labels.isChecked()
        v.set_bg_scale(self.sld_bg.value() / 100.0)
        v.update()

    # ------------------------------------------------------------------
    # 播音到游戏麦克风
    # ------------------------------------------------------------------

    def _pick_cast_file(self):
        start = os.path.dirname(self.txt_cast.text().strip()) or ''
        path, _ = QFileDialog.getOpenFileName(
            self, '选一个音频 / 视频', start, AUDIO_FILTER)
        if path:
            self.txt_cast.setText(path)
            self._save_config()

    def _select_device(self, cmb, dev_id) -> bool:
        if dev_id:
            i = cmb.findData(dev_id)
            if i >= 0:
                cmb.setCurrentIndex(i)
                return True
        return False

    def _refresh_cast_devices(self):
        """重新枚举输出设备 —— 虚拟声卡排前面并标出来。"""
        outs = broadcast.list_speakers()
        keep_out = self.cmb_cast_out.currentData() or self._cfg.get('cast_out', '')
        keep_mon = self.cmb_cast_mon.currentData() or self._cfg.get('cast_mon', '')
        for cmb in (self.cmb_cast_out, self.cmb_cast_mon):
            cmb.blockSignals(True)
            cmb.clear()
            for name, dev in outs:
                cmb.addItem(('【虚拟】' if broadcast.is_virtual(name) else '')
                            + name, dev)
            cmb.blockSignals(False)
        if not outs:
            self.txt_cast.setPlaceholderText('枚举不到输出设备（soundcard 没装好？）')
            return
        self._select_device(self.cmb_cast_out,
                            keep_out or broadcast.guess_voice_output())
        self._select_device(self.cmb_cast_mon,
                            keep_mon or broadcast.guess_headphone())

    def _cast_play(self):
        if self._caster.playing:
            self._cast_stop()
        path = self.txt_cast.text().strip()
        if not (path and os.path.isfile(path)):
            QMessageBox.information(self, '先选个文件',
                                    '点「选文件…」挑一个音频或视频。')
            return
        devs = [self.cmb_cast_out.currentData()]
        if self.chk_cast_mon.isChecked():
            devs.append(self.cmb_cast_mon.currentData())
        devs = [d for d in devs if d]
        if not devs:
            QMessageBox.warning(self, '没选设备',
                                '「送进」那里挑一个设备（虚拟声卡那类）。')
            return
        ok = self._caster.start(path, devs,
                                volume=self.sld_cast_vol.value() / 100.0)
        if not ok:
            QMessageBox.warning(self, '播不了',
                                '\n'.join(self._caster.errors) or '未知原因')
            return
        self._tick_cast.start()
        self.statusBar().showMessage(
            '正在播 %s　→　%s'
            % (os.path.basename(path), self.cmb_cast_out.currentText()))

    def _cast_stop(self):
        self._caster.stop()
        self._tick_cast.stop()
        self.lbl_cast_pos.setText('00:00.0 / 00:00.0')
        self.statusBar().showMessage('播音已停')

    def _cast_test(self):
        dev = self.cmb_cast_out.currentData()
        if not dev:
            return
        if self._caster.preview(str(dev)):
            self.statusBar().showMessage(
                '朝「%s」放了一声短音 —— 听到就说明这一路是通的'
                % self.cmb_cast_out.currentText())
        else:
            QMessageBox.warning(self, '试听失败',
                                '\n'.join(self._caster.errors) or '未知原因')

    def _update_cast(self):
        pos = self._caster.position
        dur = self._caster.duration
        self.lbl_cast_pos.setText('%s / %s' % (_fmt_time(pos), _fmt_time(dur)))
        if not self._caster.playing:
            self._tick_cast.stop()
            self.statusBar().showMessage('播完了')

    # ------------------------------------------------------------------
    # 导入音频 -> 谱面
    # ------------------------------------------------------------------

    def _pick_import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选一个音频 / 视频，自动变成谱子', '', AUDIO_FILTER)
        if path:
            self._start_import(path)

    def _start_import(self, path: str):
        if self._importing:
            self.statusBar().showMessage('上一个还在分析呢，稍等…')
            return
        self._importing = True
        self.statusBar().showMessage('正在读 %s …' % os.path.basename(path))
        self.overlay.show_toast('正在分析音频…\n（长的曲可能要等十几秒）', 120)
        import threading
        threading.Thread(target=self._import_worker, args=(path,),
                         daemon=True).start()

    def _import_worker(self, path: str):
        """在后台线程里跑 —— 别卡住界面。"""
        try:
            from core import audio_io
            audio, rate = audio_io.load_audio(path)
            if len(audio) < rate * 1.0:
                self._bridge.failed.emit('这个文件太短了（不到 1 秒）')
                return
            bpm = transcribe.guess_bpm(audio, rate) or 120
            info: dict = {}
            text, hits = transcribe.to_sheet_text(
                audio, rate, bpm=bpm, title='导入：%s'
                % os.path.splitext(os.path.basename(path))[0],
                info=info)
            self._bridge.done.emit({
                'path': path, 'text': text, 'hits': hits, 'bpm': bpm,
                'info': info, 'seconds': len(audio) / float(rate),
            })
        except Exception as e:
            self._bridge.failed.emit('%s: %s' % (type(e).__name__, e))

    def _on_import_done(self, res: dict):
        self._importing = False
        self.overlay.hide_toast()
        text = res.get('text') or ''
        hits = res.get('hits') or []
        info = res.get('info') or {}
        if not text or not hits:
            self.statusBar().showMessage('没认出音符')
            QMessageBox.information(
                self, '没认出音符',
                '这段音频里没找到这台琴的声音。\n\n'
                '常见原因：\n'
                '· 伴奏 / 人声盖过了琴声\n'
                '· 视频里琴的音量太小\n'
                '· 根本不是这台琴弹的')
            return
        self._listen_result = text
        self._open_listen()
        if self._listen is not None:
            self._listen.sp_bpm.setValue(int(res.get('bpm', 120)))
            self._listen.apply_text(text)
            self._listen.lbl_result.setText(
                '认出 <b>%d</b> 个音（起音候选 %d 个，'
                '整体跑调 %+.1f 音分已自动校正，曲速 %d BPM）'
                % (len(hits), info.get('onsets', 0),
                   info.get('offset_cents', 0.0), res.get('bpm', 120)))
        self.statusBar().showMessage(
            '已生成谱面：%d 个音 —— 检查一下，满意就「插到编辑器里」'
            % len(hits))

    def _on_import_failed(self, msg: str):
        self._importing = False
        self.overlay.hide_toast()
        self.statusBar().showMessage('导入失败')
        QMessageBox.warning(self, '导入失败', msg)

    # ------------------------------------------------------------------
    # 悬浮窗几何
    # ------------------------------------------------------------------

    def _apply_geometry(self):
        self.overlay.setGeometry(self.sp_x.value(), self.sp_y.value(),
                                 self.sp_w.value(), self.sp_h.value())
        self._capture_pin()
        self._save_config()

    # ---- 绑在游戏窗口上 ----

    def _game_rect_qt(self):
        """游戏窗口在 Qt 逻辑坐标下的 (x, y, 宽, 高)；游戏没开返回 None。

        Win32 给的是物理像素，Qt 用的是逻辑像素 —— 高 DPI 缩放下
        得除以 devicePixelRatio，不然位置会整体偏掉。
        """
        r = winfocus.game_window_rect()
        if not r:
            return None
        dpr = float(self.overlay.devicePixelRatioF() or 1.0) or 1.0
        x, y, w, h = r
        return (x / dpr, y / dpr, w / dpr, h / dpr)

    def _set_geom_silent(self, x, y, w, h):
        """改几何但不触发 spinbox 信号 —— 免得「跟随 → 保存 → 再跟随」打转。"""
        for sp, val in ((self.sp_x, x), (self.sp_y, y),
                        (self.sp_w, w), (self.sp_h, h)):
            sp.blockSignals(True)
            sp.setValue(int(val))
            sp.blockSignals(False)
        self.overlay.setGeometry(int(x), int(y), int(w), int(h))

    def _capture_pin(self) -> bool:
        """把浮窗当前位置换算成「相对游戏窗口的比例」记下来。"""
        r = self._game_rect_qt()
        if not r:
            return False            # 游戏没开 —— 保留原来记的比例，别清掉
        gx, gy, gw, gh = r
        self._pin_x = (self.sp_x.value() - gx) / max(1.0, gw)
        self._pin_y = (self.sp_y.value() - gy) / max(1.0, gh)
        return True

    def _remember_position(self):
        if self._capture_pin():
            if not self.chk_pin.isChecked():
                self.chk_pin.setChecked(True)
            self.statusBar().showMessage('已记住当前位置（会跟着游戏窗口走）')
        else:
            self.statusBar().showMessage('没找到卡拉彼丘窗口 —— 游戏开着才能绑定')
        self._save_config()

    def _on_pin_toggled(self, on: bool):
        if on:
            if not self._capture_pin():
                self.statusBar().showMessage(
                    '没找到卡拉彼丘窗口 —— 游戏开着才能绑定')
            self._last_game_rect = None
            self._tick_pin.start()
            self._follow_tick()
        else:
            self._tick_pin.stop()
        self._save_config()

    def _follow_tick(self):
        """游戏窗口一动，就按记下来的比例把浮窗挪过去。"""
        if not self.chk_pin.isChecked():
            self._tick_pin.stop()
            return
        r = self._game_rect_qt()
        if not r or r == self._last_game_rect:
            return
        self._last_game_rect = r
        gx, gy, gw, gh = r
        self._set_geom_silent(round(gx + self._pin_x * gw),
                              round(gy + self._pin_y * gh),
                              self.sp_w.value(), self.sp_h.value())

    def _snap_to(self, ax: float, ay: float):
        """贴到游戏窗口的锚点：左上 / 正中 / 右下 …"""
        r = self._game_rect_qt()
        if not r:
            self.statusBar().showMessage('没找到卡拉彼丘窗口 —— 游戏开着才能贴过去')
            return
        gx, gy, gw, gh = r
        w, h = self.sp_w.value(), self.sp_h.value()
        m = 12
        x = gx + m + max(0.0, gw - w - 2 * m) * ax
        y = gy + m + max(0.0, gh - h - 2 * m) * ay
        self._set_geom_silent(round(x), round(y), w, h)
        self._capture_pin()
        self._last_game_rect = None
        if not self.chk_pin.isChecked():
            self.chk_pin.setChecked(True)   # 贴了就顺手绑上，之后才会跟着走
        self.statusBar().showMessage('已贴到游戏窗口')
        self._save_config()

    def _on_drag_toggled(self, on: bool):
        # 顺序要紧：先 show/raise 把窗口摆好，再切换穿透样式 ——
        # 反过来的话 Qt 重新显示窗口时可能把样式冲掉，就拖不动了。
        if on:
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.set_click_through(False)
            self.statusBar().showMessage('现在可以拖动谱面窗了 —— 摆好后请取消勾选')
        else:
            self.overlay.set_click_through(True)
            self.statusBar().showMessage('已恢复鼠标穿透')
            self._sync_geom_from_overlay()

    def _sync_geom_from_overlay(self):
        g = self.overlay.geometry()
        for sp, val in ((self.sp_x, g.x()), (self.sp_y, g.y()),
                        (self.sp_w, g.width()), (self.sp_h, g.height())):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        self._capture_pin()
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
        self.sld_bg.setValue(int(cfg.get('bg_alpha', 100)))
        self.sp_preview.setValue(int(cfg.get('preview', 5)))
        self.lbl_opacity.setText('%d%%' % self.sld_opacity.value())
        self.lbl_bg.setText('%d%%' % self.sld_bg.value())
        self.overlay.set_user_opacity(self.sld_opacity.value() / 100.0)

        self.sp_count.setValue(int(cfg.get('countdown', 3)))
        self.sld_cast_vol.setValue(int(cfg.get('cast_vol', 100)))
        self.lbl_cast_vol.setText('%d%%' % self.sld_cast_vol.value())
        self.chk_cast_mon.blockSignals(True)
        self.chk_cast_mon.setChecked(bool(cfg.get('cast_mon_on', True)))
        self.chk_cast_mon.blockSignals(False)
        self.txt_cast.setText(cfg.get('cast_file', ''))
        for ed, key in ((self.hk_play, 'hk_play'),
                        (self.hk_restart, 'hk_restart'),
                        (self.hk_listen, 'hk_listen'),
                        (self.hk_toggle, 'hk_toggle')):
            ed.set_binding(*parse_binding(cfg.get(key, '不绑定')))

        for chk, key, dflt in ((self.chk_overlay, 'overlay_show', True),
                               (self.chk_only_game, 'only_game', True),
                               (self.chk_pin, 'pin', False)):
            chk.blockSignals(True)
            chk.setChecked(bool(cfg.get(key, dflt)))
            chk.blockSignals(False)
        self._pin_x = float(cfg.get('pin_x', 0.0) or 0.0)
        self._pin_y = float(cfg.get('pin_y', 0.0) or 0.0)
        self._last_game_rect = None

        last = cfg.get('last_sheet')
        if not (last and os.path.isfile(last)):
            demo = os.path.join(sheets_dir(), 'demo.txt')
            last = demo if os.path.isfile(demo) else None
        if last:
            self.load_sheet(last)
        self._apply_geometry()
        self._apply_view_options()
        self._apply_overlay_visibility()
        self._refresh_cast_devices()
        if self.chk_pin.isChecked():
            self._tick_pin.start()
            QTimer.singleShot(500, self._follow_tick)

    def _save_config(self):
        cfg = {
            'x': self.sp_x.value(), 'y': self.sp_y.value(),
            'w': self.sp_w.value(), 'h': self.sp_h.value(),
            'opacity': self.sld_opacity.value(),
            'bg_alpha': self.sld_bg.value(),
            'preview': self.sp_preview.value(),
            'last_sheet': self.path,
            'hk_play': [self.hk_play.mods, self.hk_play.vk],
            'hk_restart': [self.hk_restart.mods, self.hk_restart.vk],
            'hk_listen': [self.hk_listen.mods, self.hk_listen.vk],
            'hk_toggle': [self.hk_toggle.mods, self.hk_toggle.vk],
            'overlay_show': self.chk_overlay.isChecked(),
            'only_game': self.chk_only_game.isChecked(),
            'pin': self.chk_pin.isChecked(),
            'pin_x': round(self._pin_x, 6),
            'pin_y': round(self._pin_y, 6),
            'countdown': self.sp_count.value(),
            'cast_out': self.cmb_cast_out.currentData() or '',
            'cast_mon': self.cmb_cast_mon.currentData() or '',
            'cast_mon_on': self.chk_cast_mon.isChecked(),
            'cast_vol': self.sld_cast_vol.value(),
            'cast_file': self.txt_cast.text(),
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
        QTimer.singleShot(330, self._apply_overlay_visibility)

    def closeEvent(self, event):
        try:
            self.hotkeys.unbind_all()
        except Exception:
            pass
        self._save_config()
        try:
            self._stop_audio()
        except Exception:
            pass
        try:
            self._caster.stop()
        except Exception:
            pass
        self.overlay.hide()
        self.overlay.close()
        event.accept()
        self.close()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
