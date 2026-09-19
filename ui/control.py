# -*- coding: utf-8 -*-
"""主控制窗 —— 选谱、播放、切换显示、摆悬浮窗的位置。"""

from __future__ import annotations

import os
import subprocess
import time

import numpy as np

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import follower, layout, parser, timeline, winfocus
from core.paths import APP_NAME, all_sheets, bundled_dir, sheets_dir

from . import appstyle, config
from .control_build import BuilderMixin
from .editor import EditorDialog
from .hotkeys import (HK_PLAYPAUSE, HK_RESTART, HK_TOGGLE, HotkeyEdit,
                      HotkeyManager, parse_binding)
from .keypad import NotePlayer
from .overlay import OverlayWindow, Player, TrainWindow


def _fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    return '%02d:%04.1f' % (m, sec - m * 60)


def _sheet_name(path: str | None, title: str | None = None) -> str:
    """浮窗顶上那行曲名显示什么。

    ★ 不带 `.txt` ★
      浮窗默认才 470 px 宽，那一行还要居中留白，后缀纯属占地方
      （文件列表里带后缀是有用的，那里另说）。

    ★ 谱面里写了标题就用标题 ★
      跟控制台里「已载入 XXX」用的是同一个优先级，
      免得同一个文件在两个地方叫两个名字。
    """
    if title:
        return str(title).strip()
    name = os.path.splitext(os.path.basename(path or ''))[0].strip()
    return name or '还没有载入谱面'


def _editor_name(dlg) -> str:
    """制谱器那边正在编的是哪一份 —— 浮窗曲名行用的。

    ★ 加个前缀 ★
      浮窗那行字现在可能来自两个地方（控制台载入的、制谱器手里的），
      光看名字分不出这是"浮窗现在跟谁走"。三个字换一个明确的来源提示。
    """
    p = getattr(dlg, 'path', '') or ''
    name = os.path.splitext(os.path.basename(p))[0].strip()
    return ('制谱器：%s' % name) if name else '制谱器（还没保存）'


# 跟手模式留给「估基频」的滚动窗口（样本）。2880 = 60 ms @ 48 kHz。
#
# 「贴到游戏窗口的锚点」已随绑定功能一起去掉（用户：「这个可以去掉」）——
# 现在浮窗位置全靠**拖动手柄**（顶上那条「⠿」）、大小滑块、
# 或者勾「允许拖动」之后直接抓窗口本体。
# 注：X·Y·宽·高 四个数字框和「微调按钮」也已经拿掉了（见 `_build`），
#     这里的注释别再写它们。

# ★ 新建谱面时写进文件的那句说明 ★
#   必须写成"解析器认不出来"的样子：`parser` 认 `秒:音高`（英文冒号）
#   和裸 `#`，所以说明里**不能出现**这两样 —— 否则用户一按「新建谱面」
#   就凭空多出几个幽灵音符。
#   实测：写一句 `# 例：0:1 0.5:5 1:6 1.5:5` 会解析出 5 个音
#   （`#` 自己也算一个），而且浮窗会报"琴上没有"，看着莫名其妙。
#   全中文 + 中文标点，整段都会被跳过，**也不占时间**。
_NEW_SHEET_HINT = (
    '这一行是说明，会被自动跳过，可以删掉。'
    '写音符的格式是「第几秒」加一个英文冒号再加「键名」，'
    '一行一个音、或者一行好几个都行。'
)


class ControlWindow(BuilderMixin, QMainWindow):
    def __init__(self, player: Player, overlay: OverlayWindow):
        super().__init__()
        self.player = player
        self.overlay = overlay
        self.tl: timeline.Timeline | None = None
        self.path: str | None = None

        self.setWindowTitle('%s — 控制台' % APP_NAME)
        self.resize(640, 680)

        self._cfg: dict = {}

        # 前台窗口轮询 —— 让浮窗「只在卡丘窗口在前面时才露面」
        self._fg_last = None
        self._tick_fg = QTimer(self)
        self._tick_fg.setInterval(500)
        self._tick_fg.timeout.connect(self._poll_foreground)

        # 「绑在游戏窗口上」的记比例方案已删 —— 这里原来留着一个空的
        # `_handles` 列表，全项目只写不读，一并清掉。
        self._editor = None          # 制谱器（打开着的时候）
        self._editor_tl = None       # 从制谱器拿来的那份 Timeline（缓存）

        # 全局热键挂在谱面窗上 —— 它常驻、置顶、且不会抢焦点
        self.hotkeys = HotkeyManager(overlay, self)
        overlay.hotkeys = self.hotkeys
        self.hotkeys.fired.connect(self._on_hotkey)

        # ★ 「制谱器试听」用的推进器 ★
        #   在制谱器里点打击垫 → 浮窗往前走一格（`_on_pad_hit`）。
        #   推进规则本身在 `core/follower.py`（纯逻辑、能单测），这里只接上。
        #   （它跟"听音"没有关系 —— 驱动它的是一次**点击**，不是麦克风。）
        self._follow = None

        # ★ 音源 —— 「播放声音」用的 ★
        #   懒载入：第一次打开那个开关才建。`NotePlayer.load_all()` 走的
        #   `synth.ensure_notes()` 在第一次运行时还要现生成 16 个 wav，
        #   没开这个功能的人一点开销都不该摊上。
        self._notes = None

        # ★ 「训练」★
        #   用户：「跟打功能旁边再增加一个训练功能，具体就是去点按键，
        #   但是不是按照曲子顺序来是按照音符顺序来，自己点，
        #   点一个继续下一个」+「点这个旁边会直接显示另外一个铺面，
        #   之前那个铺面可以用来预览」。
        #
        #   所以它是**独立的一块窗**（`TrainWindow`），跟谱面窗并排摆：
        #   一边练、一边看。谱面窗一点不动，它继续当预览。
        #   跟「跟打」的区别：跟打看**时间**，训练看**顺序**。
        self.train = TrainWindow()
        self.train.grid.pad_pressed.connect(self._on_train_pad)
        self._train_seq: list[str] = []      # 摊平之后的音名序列
        self._train_gaps: list[float] = []   # 每个音到下一个的间隔（秒）
        self._train_i = 0                    # 练到第几个了

        # ★ 主题要在 `_build()` **之前**装 ★
        #   `setStyle('Fusion')` 对已经建好的控件不生效，
        #   建完再装就是"半新半旧"。幂等，`main.py` 里那次已经装过也没关系。
        appstyle.apply(QApplication.instance())

        self._build()
        self._build_sidebar()
        self._wire()
        self._load_config()
        self._scan_sheets()

    def _open_sheets_dir(self):
        try:
            os.startfile(sheets_dir())      # noqa: S606（Windows 专用，没问题）
        except Exception as e:
            QMessageBox.warning(self, '打不开文件夹', str(e))

    def _fill_sidebar(self):
        """把仓库里的曲谱填进侧栏，并标出当前这份。"""
        lst = getattr(self, 'list_sheets', None)
        if lst is None:
            return
        lst.blockSignals(True)
        lst.clear()
        cur = os.path.abspath(self.path) if self.path else ''
        hit = None
        for path in all_sheets():
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            if os.path.abspath(path) == cur:
                item.setText('✅ ' + item.text())
                item.setSelected(True)
                hit = item
            lst.addItem(item)
        lst.blockSignals(False)
        if hit is not None:
            lst.scrollToItem(hit)

    def _on_sidebar_pick(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.abspath(path) != os.path.abspath(self.path or ''):
            self.load_sheet(path)

    def _status_refresh_sidebar(self):
        """载入完顺手把侧栏的勾挪过来（顺便刷新列表）。"""
        self._fill_sidebar()

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------

    def _wire(self):
        self.btn_new.clicked.connect(self._new_sheet)
        self.btn_open.clicked.connect(self._open_dialog)
        self.btn_edit.clicked.connect(self._edit_sheet)
        self.btn_reload.clicked.connect(self._reload)
        self.btn_live.clicked.connect(self._toggle_follow_sheet)
        self.cmb_sheet.currentIndexChanged.connect(self._pick_from_combo)

        # ★ 曲谱侧栏：删谱面 ★ —— 用户：「这里得支持删谱面的选项」
        self.btn_del_sheet.clicked.connect(lambda *_: self._delete_sheet())
        self.list_sheets.customContextMenuRequested.connect(self._sheet_menu)

        self.btn_play.clicked.connect(self.player.toggle)
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_back.clicked.connect(lambda: self.player.seek(0.0))
        # （「后退 / 前进 0.5 秒」两个按钮删掉了 —— 微调位置拖进度条就行）
        # ★ 「播放声音」—— 控制台里的勾选框和浮窗顶上那颗 🔊 是同一个开关 ★
        #   两条线都接到 `_set_sound`，由它一处说了算（包括音源的懒载入
        #   和"两边控件状态对齐"），别在两头各写一份。
        self.chk_sound.toggled.connect(self._set_sound)
        # ★ 「跟打」「可按」—— 控制台这两个勾选框跟浮窗上那两颗按钮是
        #   同一对开关，两条线都接到同一处，由它一处说了算 ★
        self.chk_karaoke.toggled.connect(self._set_karaoke)
        self.chk_pad_click.toggled.connect(self._set_pad_click)

        self.sld_prog.sliderReleased.connect(self._seek_from_slider)
        self.sld_prog.sliderPressed.connect(lambda: self.player.pause())

        self.sp_preview.valueChanged.connect(self._apply_view_options)
        self.sld_opacity.valueChanged.connect(self._on_opacity_changed)
        self.sld_bg.valueChanged.connect(self._on_bg_changed)
        self.chk_labels.toggled.connect(self._apply_view_options)

        for sp in (self.sp_x, self.sp_y, self.sp_w, self.sp_h):
            sp.valueChanged.connect(self._apply_geometry)
        self.sld_size.valueChanged.connect(self._on_size_changed)
        # ★ 浮窗**始终**鼠标穿透 ★
        #   「允许拖动（临时取消鼠标穿透）」那个勾选框已经删掉
        #   （用户：「这个选项也没有用了」）。要挪位置就拖顶上那条
        #   「⠿」手柄 —— 只有它不穿透，别的都让枪打过去。
        self.overlay.set_click_through(True)
        # ★ 浮窗顶上那条控制条 ★
        #   它只发信号（选曲 / 进度 / 播放暂停），认谱面和播放器的事都在
        #   这边接 —— 跟 `player.tick → overlay.set_time` 是同一条路子。
        self.overlay.handle.play_toggled.connect(self.player.toggle)
        self.overlay.handle.seek_requested.connect(self._on_overlay_seek)
        self.overlay.handle.pick_requested.connect(self._pick_sheet_menu)
        self.overlay.handle.sound_toggled.connect(self._set_sound)
        # ★ 浮窗上那两颗新模式按钮 + 点格子出声 ★
        #   用户：「新增一个跟打和激活为可点按发声的按钮」。
        self.overlay.handle.karaoke_toggled.connect(self._set_karaoke)
        self.overlay.handle.pad_click_toggled.connect(self._set_pad_click)
        self.overlay.grid_view.pad_pressed.connect(self._on_overlay_pad)
        # ★ 「训练」——同一个按钮信号，接到训练那边 ★
        self.overlay.handle.train_toggled.connect(self._set_train)
        self.overlay.handle.drag_started.connect(self._on_handle_drag_start)
        self.overlay.handle.drag_finished.connect(self._on_handle_drag_finish)

        self.player.tick.connect(self._on_tick)
        self.player.state_changed.connect(self._on_state)

        for ed in (self.hk_play, self.hk_restart, self.hk_toggle):
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
        self._fill_sidebar()

    def _pick_from_combo(self, _idx):
        path = self.cmb_sheet.currentData()
        if path:
            self.load_sheet(path)

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '打开谱面', sheets_dir(), '谱面文件 (*.txt);;所有文件 (*)')
        if path:
            self.load_sheet(path)

    def _new_sheet(self):
        """新建一份空白谱面，起好名字直接进制谱器。

        ★ 补一个缺掉的入口 ★
          用户：「这里没有新建铺面选项」。
          以前想写新曲子只有两条路 —— 把 .txt 丢进 sheets 文件夹再按
          「刷新」，或者拿现成的一份「另存为」。都没有"从零开始"。
        """
        name, ok = QInputDialog.getText(
            self, '新建谱面', '起个名字（会存成 sheets 里的 .txt）：',
            QLineEdit.EchoMode.Normal, '新谱面')
        if not ok:
            return
        path = self._create_sheet(name)
        if not path:
            return
        self._scan_sheets()
        # ★ 这里**不调** `load_sheet()` ★
        #   新建出来的是空谱面，而 `load_sheet()` 开头就会弹一句
        #   「这份谱子是空的」把人拦下来 —— 可我们本来就是要从零开始，
        #   那条警告在这儿纯属帮倒忙。直接把路径交给制谱器：
        #   写完保存之后（`_edit_sheet` 返回的那一段）自然会载入。
        self.path = path
        self._edit_sheet()

    def _create_sheet(self, name: str) -> str | None:
        """按名字建一份谱面文件，返回它的路径；没成就返回 `None`。

        ★ 为什么单独抽出来 ★
          `_new_sheet()` 一上来要弹输入框（阻塞），测试点不了它。
          把"问名字"和"建文件"拆开之后，建文件这一段就能直接测了。
        """
        name = (name or '').strip() or '新谱面'
        # 文件名里不能出现的字符换掉，别让人撞一鼻子灰
        for ch in '\\/:*?"<>|':
            name = name.replace(ch, '_')
        path = os.path.join(sheets_dir(), '%s.txt' % name)

        if os.path.exists(path):
            ans = QMessageBox.question(
                self, '已经有一份了',
                '「%s」已经存在。要打开它接着改吗？'
                % os.path.basename(path))
            if ans != QMessageBox.StandardButton.Yes:
                return None
            return path

        try:
            # ★ 骨架里**不能有**解析器认得的东西 ★
            #   说明见 `_NEW_SHEET_HINT` 上面那段 —— 写错一个冒号
            #   或者一个 `#`，新建出来的谱面就自带几个幽灵音符。
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('%s\n%s\n' % (name, _NEW_SHEET_HINT))
        except Exception as e:
            QMessageBox.warning(self, '新建失败', str(e))
            return None
        return path

    def _reload(self):
        if self.path:
            self.load_sheet(self.path)

    def _edit_sheet(self):
        """打开制谱器（**非模态**）。

        ★ 为什么不再用 `exec()` ★
          用户：「这个窗口在前面的时候需要这个窗口也可以拖动」，
          确认后是"制谱器开着时，控制台也要能拖 / 能点"。
          模态对话框会把控制台整个冻住 —— 拖不动、点不了，
          想把它挪开看看谱面都不行。
          改成 `show()` 之后两个窗口各干各的：制谱器里改谱面，
          控制台照样能拖、能挑曲子、能调浮窗大小位置。
          （"保存后刷新"那一小段本来写在 `exec()` 后面 ——
           非模态没有那一刻了，挪进关闭回调。）
        """
        dlg = EditorDialog(self.path, self)
        dlg.setModal(False)
        self._editor = dlg
        # ★ 浮窗的"按键显示跟随制谱器"就是这一根线 ★
        #   制谱器只管报"我到第几秒了"，浮窗画什么由这边决定 ——
        #   制谱器不需要知道浮窗长什么样。
        dlg.timeline_tick.connect(self._on_editor_tick)
        # ★ 打击垫点击 —— 浮窗跟着它走（见 `_on_pad_hit`）★
        dlg.pad_hit.connect(self._on_pad_hit)

        def _on_closed(_result=0):
            self._close_editor(dlg)
            if dlg.saved and dlg.path:
                self._scan_sheets()
                self.load_sheet(dlg.path)

        dlg.finished.connect(_on_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _close_editor(self, dlg):
        """制谱器关掉 —— 浮窗还回控制台自己载入的那份谱面。"""
        if getattr(self, '_editor', None) is None:
            return
        self._editor = None
        self._editor_tl = None
        self._editor_song = None
        # 制谱器那条路建的推进器跟着一起收掉
        self._follow = None
        if self.tl is not None:
            self.overlay.set_timeline(self.tl)
            self.overlay.set_time(self.player.sec)
            self.overlay.set_song_name(_sheet_name(self.path))
        else:
            self.overlay.set_song_name('还没有载入谱面')
        # ★ 制谱器的 parent 是控制台窗口，`exec()` 返回后它还被父窗口持有 ★
        #   不 deleteLater 的话，`_blink`(40ms) / `_watch`(150ms) 那两个
        #   QTimer 会跟着这个对象一直活着 —— 关掉制谱器之后它们照样在跑。
        #   （定时器本身也在 `EditorDialog.closeEvent` 里停了，这里是双保险：
        #     把对象真正回收掉，别攒一堆关不掉的对话框。）
        try:
            dlg.deleteLater()
        except Exception:
            pass

    def _on_editor_tick(self, sec: float):
        """制谱器报来的位置 —— 让浮窗的按键显示跟着它走。

        用户：「在制谱器播放的时候这个窗口要跟着制谱器来」「按键显示跟随」。
        """
        if not self.chk_follow_editor.isChecked():
            return
        dlg = getattr(self, '_editor', None)
        if dlg is None:
            return
        tl = dlg.overlay_timeline()
        if tl is None:
            return
        if tl is not self._editor_tl:          # 谱面改了才换时间轴
            self._editor_tl = tl
            self.overlay.set_timeline(tl)
        self._sync_editor_song(dlg)
        self.overlay.set_time(max(0.0, float(sec)))

    def _sync_editor_song(self, dlg):
        """浮窗曲名行切到制谱器手里那份（名字没变就不动它）。

        ★ 为什么要记 `_editor_song` ★
          `_on_editor_tick` 是播放时每 12 ms 来一次，
          每次都 `setText` 会让那一行反复重绘 —— 名字没变就别碰。
        """
        name = _editor_name(dlg)
        if name != getattr(self, '_editor_song', None):
            self._editor_song = name
            self.overlay.set_song_name(name)

    def _on_pad_hit(self, pitches):
        """制谱器的打击垫被点了 —— 浮窗**立刻跟着走**。

        用户：「你直接让这个的逻辑跟着**打击垫**走就行」。

        ★ 这条为什么比"听声音"强得多 ★

          打击垫的点击是**同步事件**：你点了哪个键，程序当场就知道。
          不用听、不用认、不会被 BGM 骗 —— 所以这条路上
          **零延迟、零误报、零猜测**。

          对比另外两条路：
            · 「🎵 跟手」 听声音判断你敲了 → 要音频，环境一有音乐就乱
            · 「📜 跟谱面」按时间走       → 你弹得比谱面快就一直落后
          而这条：**你点一下，它走一格**，而且准确知道是哪个键。

        ★ 它补的是「浮窗跟随制谱器」漏掉的那一半 ★

          `_on_editor_tick` 报的是**播放头到哪儿了**，而点打击垫
          **不改播放头**。所以以前在制谱器里连点 5 下，浮窗一直
          停在原地 —— 看着就跟"落后好几个"一样。
        """
        try:
            ps = [str(p) for p in (pitches or []) if p]
        except Exception:
            ps = []
        if not ps:
            return

        # ★ 1. 先闪你刚点的键 —— 立刻，不等任何东西 ★
        #   闪的是**你实际点的那个键**，不是猜出来的。所以这里
        #   既不需要音高闸门、也不需要置信度 —— 那些东西存在的
        #   唯一理由就是"从声音里猜"，这条路上没有要猜的东西。
        for p in ps:
            try:
                self.overlay.flash_note(p, seconds=0.28, min_gap=0.02)
            except Exception:
                pass

        # ★ 2. 把浮窗的谱面刷成最新的 ★
        #   制谱器是**边点边长**的：每点一下谱面就多一个音。
        #   `_on_editor_tick` 只在播放时才刷新，所以这里得自己来。
        tl = None
        dlg = getattr(self, '_editor', None)
        if dlg is not None:
            try:
                tl = dlg.overlay_timeline()
                if tl is not None and tl is not self._editor_tl:
                    self._editor_tl = tl
                    self.overlay.set_timeline(tl)
                self._sync_editor_song(dlg)
            except Exception:
                tl = None
        if tl is None:
            tl = self._editor_tl or self.tl
        if not (tl and tl.items):
            return

        # ★ 3. 位置往前走一格 ★
        f = self._follow
        if f is None:
            f = self._follow = follower.Follower(len(tl.items))
        else:
            # 谱面刚长长了 —— 只更新总数，**位置留着**
            # （重建一个的话位置会回到开头，越点越退）
            f.count = len(tl.items)
            if f.index >= f.count:
                f.reset()        # 换成一份更短的谱面了 —— 从头跟
        # ★ 用 `press()` 而不是 `kick()` ★
        #   打击垫点击是真实事件，不该被那 40 ms 的"听声音去抖"挡掉 ——
        #   那个兜底是给"一次敲击的能量被上报两三次"准备的，
        #   而这里一个点击就是一个点击，不存在重复上报。
        if not f.press():
            return
        i = min(f.index, len(tl.items) - 1)
        self.overlay.set_time(tl.items[i].start_sec)

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
        # ★ 浮窗那行曲名跟着换 ★ —— 用户：「下面要显示哪首」
        self.overlay.set_song_name(_sheet_name(path, sheet.title))

        bad = layout.unmapped_pitches(self.tl.all_pitches())
        txt = '<b>%s</b>　%s' % (sheet.title or os.path.basename(path),
                                 self.tl.stats())
        if bad:
            txt += ('<br><span style="color:#ff9a9a">琴上没有的音'
                    '（会跳过）：%s</span>' % ' '.join(bad))
        self.lbl_sheet.setText(txt)
        self._status_refresh_sidebar()
        self.statusBar().showMessage('已载入 %s' % os.path.basename(path))
        self._save_config()
        # ★ 换谱面时训练要跟着重来 ★
        #   训练序列是**载入那一刻**从谱面摊平出来的（`_train_sequence`）。
        #   换了谱面而序列不换的话，会拿着旧曲子的音名去对新的点击 ——
        #   用户看到的是"我明明按对了它说不是这个"。
        #   重来（而不是关掉）是因为换谱面多半就是"这首练完了换一首"。
        if self.overlay.handle.btn_train.isChecked():
            self._set_train(True)

    # ------------------------------------------------------------------
    # ★ 删谱面 ★
    # ------------------------------------------------------------------

    def _sheet_at(self, item=None) -> str | None:
        """侧栏某一项对应的文件路径（不传就用当前选中的那项）。"""
        lst = getattr(self, 'list_sheets', None)
        if lst is None:
            return None
        it = item if item is not None else lst.currentItem()
        if it is None:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _is_builtin(self, path: str) -> bool:
        """这份谱子是不是**内置**的（在程序自己带的那个目录里）。

        ★ 为什么内置的不让删 ★
          内置谱面在 `_internal/sheets`（源码运行时就是项目根的 `sheets`）。
          删了它，下次重新打包 / 更新又回来 —— 与其留一个"删了还在"的
          按钮让人反复试，不如当场说清楚。
        """
        try:
            p = os.path.abspath(path).lower()
            user = os.path.abspath(sheets_dir()).lower()
            built = os.path.abspath(
                os.path.join(bundled_dir(), 'sheets')).lower()
        except Exception:
            return False
        if os.path.dirname(p) == user:
            return False                     # 在用户的谱面文件夹里 —— 能删
        return p.startswith(built + os.sep)

    def _unload_sheet(self):
        """当前那份被删掉了 —— 把曲子、时间轴、浮窗都退回"还没载入"。"""
        self.tl = None
        self.path = None
        self.player.stop()
        self.player.set_timeline(None)
        self.overlay.set_timeline(None)
        self.overlay.set_song_name('还没有载入谱面')
        self.lbl_sheet.setText('还没有载入谱面')
        self.lbl_time.setText('00:00.0 / 00:00.0')
        # 谱面都没了，训练序列就是一堆对不上号的音名 —— 关掉它，
        # 别留一块"点什么都不对"的面板挂在那儿。
        if self.overlay.handle.btn_train.isChecked():
            self._set_train(False)

    def _delete_sheet(self, item=None):
        """把侧栏里那份谱面**连文件一起**删掉。

        用户：「这里得支持删谱面的选项」。

        ★ 删之前一定问一次 ★
          `os.remove` 没有回收站，删了就真没了。
        """
        path = self._sheet_at(item)
        if not path:
            self.statusBar().showMessage('先在列表里点一份谱面，再按这个按钮')
            return
        name = os.path.basename(path)
        if self._is_builtin(path):
            QMessageBox.information(
                self, '这份删不掉',
                '「%s」是程序自带的内置谱面，不在你的谱面文件夹里。\n\n'
                '删了它，下次重新打包 / 更新还会回来，'
                '所以这里干脆不让删。' % name)
            return
        yes = QMessageBox.question(
            self, '删掉这份谱面？',
            '「%s」会被连文件一起删掉，撤销不了。\n\n确定要删吗？' % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if yes != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(path)
        except Exception as e:
            QMessageBox.warning(self, '删不掉',
                                '文件被占用，或者没有权限：\n%s' % e)
            return
        # ★ 删掉的正好是当前载入的那份 → 别让它继续挂在播放器上 ★
        if self.path and os.path.abspath(self.path) == os.path.abspath(path):
            self._unload_sheet()
        self._scan_sheets()
        self.statusBar().showMessage('已删掉 %s' % name)

    def _reveal_file(self, path: str):
        """在资源管理器里选中这个文件（比只打开文件夹好找）。"""
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
        except Exception as e:
            QMessageBox.warning(self, '打不开文件夹', str(e))

    def _sheet_menu(self, pos):
        """曲谱列表上点右键 —— 删除 / 在文件夹里显示。"""
        lst = getattr(self, 'list_sheets', None)
        if lst is None:
            return
        item = lst.itemAt(pos)
        if item is None:
            return
        # 右键顺带选中（Windows 上的习惯），菜单里的操作才有明确的宾语
        lst.setCurrentItem(item)
        path = self._sheet_at(item)
        if not path:
            return
        menu = QMenu(self)
        builtin = self._is_builtin(path)
        act_del = menu.addAction('🗑  删掉这份谱面')
        act_del.setEnabled(not builtin)
        if builtin:
            act_del.setToolTip('内置谱面删不掉')
        menu.addSeparator()
        act_reveal = menu.addAction('📂  在文件夹里显示')
        picked = menu.exec(lst.viewport().mapToGlobal(pos))
        if picked is None:
            return
        if picked is act_del:
            self._delete_sheet(item)
        elif picked is act_reveal:
            self._reveal_file(path)

    # ------------------------------------------------------------------
    # 播放 / 显示
    # ------------------------------------------------------------------

    def _seek_from_slider(self):
        if not self.tl:
            return
        self.player.seek(self.sld_prog.value() / 1000.0 * self.tl.total_sec)

    def _on_overlay_seek(self, ratio: float):
        """浮窗顶上那条进度被拖了 —— `ratio` 是 0~1。

        ★ 跟 `_seek_from_slider` 一个意思，只是滑块在浮窗上 ★
          两条进度条共用同一个播放器，所以拖哪条都一样。
        """
        if not self.tl:
            return
        r = max(0.0, min(1.0, float(ratio)))
        self.player.seek(r * self.tl.total_sec)

    def _set_sound(self, on: bool):
        """「播放声音」—— 浮窗按谱面往前走的时候同步放出琴音。

        用户：「应该做成和音乐播放器一样的，然后多一个选项，播放声音」。

        ★ 在这之前是**纯时钟** ★
          点 ▶ 只有格子依次亮过去，一点声音都没有 —— 而按钮的说明里
          写着「适合先听几遍熟悉节奏」，没声音根本听不了。现在把
          `ui/keypad.py` 那套音源（跟打击垫同一个音色）接到播放器上。

        ★ 音源是懒载入的 ★
          第一次打开才 `NotePlayer.load_all()` —— 它里面走
          `synth.ensure_notes()`，第一次运行还要现生成 16 个 wav。
          不打开这个功能的人，启动一点额外开销都不该有。

        ★ 两处控件都对齐到**播放器实际的状态** ★
          音源载不出来时 `player.sound_on` 会是假 —— 那两个开关
          得跟着弹回去，不能留着"勾上了但没声音"这种假象。
        """
        on = bool(on)

        if on and not self._ensure_notes():
            self.player.sound = None
            self.player.set_sound_on(False)
            self.chk_sound.blockSignals(True)
            self.chk_sound.setChecked(False)
            self.chk_sound.blockSignals(False)
            self.overlay.set_sound(False)
            return

        self.player.sound = self._notes
        self.player.set_sound_on(on)

        self.chk_sound.blockSignals(True)
        self.chk_sound.setChecked(self.player.sound_on)
        self.chk_sound.blockSignals(False)
        self.overlay.set_sound(self.player.sound_on)
        self._save_config()

    def _ensure_notes(self) -> bool:
        """要音源 —— 没有就现载一个。返回"现在能用吗"。

        ★ 懒载入 ★
          `NotePlayer.load_all()` 里走 `synth.ensure_notes()`，
          第一次运行还要现生成 16 个 wav。用不到这个功能的人，
          启动一点额外开销都不该有。

        （原来这段逻辑长在 `_set_sound` 里；跟打要用同一份音源，
          就抽出来两边共用了。）
        """
        if self._notes is not None:
            return True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            notes = NotePlayer()
            n = notes.load_all()
        except Exception:
            notes, n = None, 0
        finally:
            QApplication.restoreOverrideCursor()
        if n <= 0:
            self.statusBar().showMessage(
                '音源载入失败 —— 出声用不了（检查 assets/notes）')
            return False
        self._notes = notes
        self.statusBar().showMessage('音源已载入（%d 个音）' % n)
        return True

    # ---- ★ 跟打 / 可按 ★ ----

    def _on_overlay_pad(self, pitch: str):
        """在浮窗上点了一个格子 —— 出那个音。

        用户：「激活可按就是让按键可以被点击发声」。
        音源跟制谱器的打击垫、跟「播放声音」是同一套（`NotePlayer`）。
        """
        if not pitch:
            return
        if self._ensure_notes():
            try:
                self._notes.play(pitch)
            except Exception:
                pass
        # 顺手让那个格子亮一下 —— 点了没反馈，会让人以为没点到
        try:
            self.overlay.flash_note(pitch, seconds=0.25, min_gap=0.02)
        except Exception:
            pass

    def _set_pad_click(self, on: bool):
        """「可按」—— 浮窗的格子能不能被点。

        ★ 它动的是**整个窗口的鼠标穿透** ★
          浮窗平时带 `WS_EX_TRANSPARENT`，鼠标直接穿过去（枪的准星
          要能打过去）。要让格子能点，只能把整窗的穿透取消 ——
          这是 Windows 层面的限制，没法只让"格子那块"接收鼠标。
          所以它是个**临时**状态：点完记得关，不然会挡住游戏画面。
        """
        on = bool(on)
        try:
            self.overlay.set_click_through(not on)
        except Exception:
            pass
        gv = getattr(self.overlay, 'grid_view', None)
        if gv is not None:
            gv.pad_click = on
        self.overlay.handle.set_pad_click(on)
        self.chk_pad_click.blockSignals(True)
        self.chk_pad_click.setChecked(on)
        self.chk_pad_click.blockSignals(False)
        # ★ 一定等 `chk_pad_click` 落定之后再算 ★
        #   第一版把这个调用放在上面几行之前，`_refresh_keys_only()`
        #   读到的还是**旧值**，于是"只开可按"永远算不出 `keys_only` ——
        #   现象是按钮亮了、格子也能点，可谱面高亮照旧赖着不走。
        self._refresh_keys_only()
        self.chk_pad_click.blockSignals(True)
        self.chk_pad_click.setChecked(on)
        self.chk_pad_click.blockSignals(False)
        # ★ 在浮窗上闪一条 ★
        #   用户报「可按功能有问题，按不了」的时候，光看那颗按钮
        #   看不出它到底开没开 —— 而且"开了但格子没反应"和
        #   "压根没开"这两种情况，屏幕上长得一模一样。
        #   闪一条之后：**没看到这条 = 按钮那一下没生效**，
        #   看到了还点不响 = 事件没到格子上，两种毛病当场就分开了。
        try:
            self.overlay.show_toast(
                '可按已开：点格子出声（鼠标不再穿透浮窗）' if on
                else '可按已关：鼠标重新穿透浮窗', 2.0)
        except Exception:
            pass
        self.statusBar().showMessage(
            '可按：开着 —— 浮窗的格子能点了（鼠标不再穿透）' if on
            else '可按：关着 —— 浮窗恢复鼠标穿透')

    def _set_karaoke(self, on: bool):
        """「跟打」—— 播放时不放原声，改成你点格子出声。

        用户：「跟打就是在播放的时候点按出声，不会播放原声」。
        练琴用：听着自己弹的，才听得出来哪儿按错了。

        ★ 它**不改**「播放声音」那个设置 ★
          那个开关是要存进配置的。跟打只是"这一会儿不放原声"，
          两件事合在一起的话，练一次琴就顺手把用户的设置改掉了。
          所以走的是 `player.karaoke` 这个临时标志（不落盘）。
        """
        on = bool(on)
        self.player.karaoke = on
        # 跟打必须能点 —— 不然"你点出声"这件事没法发生
        self._set_pad_click(on)
        self.overlay.handle.set_karaoke(on)
        self.chk_karaoke.blockSignals(True)
        self.chk_karaoke.setChecked(on)
        self.chk_karaoke.blockSignals(False)
        if on:
            self.statusBar().showMessage(
                '跟打：开着 —— 播放不放原声，点浮窗上的格子才出声')
        else:
            self.statusBar().showMessage('跟打：关着 —— 浮窗恢复鼠标穿透')
        # ★ 再算一次"浮窗画不画谱面提示" ★
        #   上面那句 `_set_pad_click(on)` 里已经算过一次，可那时候
        #   `chk_karaoke` 还是**旧值**（这里还没更新），算出来是错的。
        #   等三个状态都落定之后再算一遍才准。
        self._refresh_keys_only()

    def _refresh_keys_only(self):
        """「可按」**单独**开着的时候，浮窗只当琴键用，不显示谱面提示。

        用户连着报了两轮：

            可按的时候不要显示高亮
            可按的时候高亮没有消失

        要的就是这个（第一轮我以为是那颗按钮自己的高亮，改错了 ——
        见 `DragHandle._build_bar` 里 `btn_tap` 那段）。

        ★ 为什么「跟打」不算 ★
          跟打就是"照着谱面打"，提示必须留着 —— 把高亮收掉的话，
          用户就不知道该打哪个键了，那是功能倒退。
          所以判据是：**可按开着 而且 跟打关着**。

        ★ 三条路都会走到这儿 ★
          控制台的勾选框、浮窗把手上的按钮、`_set_karaoke` 里的联动，
          最后都汇到 `_set_pad_click` —— 放在那儿算一次就都覆盖了。
        """
        gv = getattr(self.overlay, 'grid_view', None)
        if gv is None:
            return
        solo = bool(self.chk_pad_click.isChecked()
                    and not self.chk_karaoke.isChecked())
        if gv.keys_only == solo:
            return                      # 没变就别白白重绘一遍
        gv.keys_only = solo
        gv.update()

    # ---- ★ 训练 ★ ----
    #
    #   用户：「跟打功能旁边再增加一个训练功能，具体就是去点按键，
    #   但是不是按照曲子顺序来是按照音符顺序来，自己点，
    #   点一个继续下一个」。
    #
    #   跟「跟打」的区别一句话：
    #     跟打看**时间** —— 播放进度在跑，你得跟上；
    #     训练看**顺序** —— 第 1、2、3… 个音，点对当前这个才亮下一个。
    #   也就是把"什么时候点"交给谱面顺序、"来不来得及"交给自己。

    def _train_sequence(self) -> tuple[list[str], list[float]]:
        """训练序列 + 每个音的间隔 —— 谱面里所有音按**先后顺序**摊平。

        ★ 和弦展开成单个音 ★
          用户要的就是"按音符顺序来"。一个和弦 `1&3&5` 里有三个音，
          在训练里就是连着点三下 —— 这样"下一个是哪个"永远有唯一答案，
          点对点错一眼就知道。
          不这么做的话，"和弦算一个还是算三个"就成了要额外解释的规则，
          而训练最不需要的就是规则。

        ★ 休止符跳过 ★
          它没有键可按。"下一个"必须是点得到的东西。

        ★ 间隔就是这个音到下一个音的时间差 ★
          用户：「然后需要标记间隔时间的这样子直观」。
          训练不看时间，但"这两个音之间隔多久"是**曲子的一部分** ——
          只练按键顺序不练节奏，练出来的不是那首曲子。
          和弦内部那几个音的差是 0，到 `_train_gap_at` 那边会被夹到
          下限（它们本来就该连着按下去）。
        """
        if not self.tl:
            return [], []
        starts: list[float] = []          # 摊平之后每个音的开始时间
        seq: list[str] = []
        for item in self.tl.items:
            if item.chord.is_rest:
                continue
            for p in item.chord.pitches:
                seq.append(p)
                starts.append(item.start_sec)
        gaps: list[float] = []
        for i in range(len(starts)):
            if i + 1 < len(starts):
                gaps.append(starts[i + 1] - starts[i])
            else:
                # 最后一个音后面没东西了 —— 给个默认，免得圈缩完就停在那儿
                gaps.append(0.5)
        return seq, gaps

    def _set_train(self, on: bool):
        """开 / 关训练。开的时候在谱面窗**右边**摆一块训练面板。"""
        on = bool(on)
        g = self.train.grid
        self.overlay.handle.set_train(on)
        if not on:
            g.train_on = False
            self._train_tick_stop()
            self.train.hide()
            self.statusBar().showMessage('训练：关着')
            return
        seq, gaps = self._train_sequence()
        if not seq:
            # 没谱面（或者谱面里一个音都没有）—— 把按钮弹回去，
            # 别留一个"开着但什么也不显示"的状态在那儿骗人。
            self.overlay.handle.set_train(False)
            self.statusBar().showMessage(
                '训练：没有谱面（或者谱面里一个音都没有）')
            return
        self._train_seq = seq
        self._train_gaps = gaps
        self._train_i = 0
        # ★ 序列和间隔交给面板，它自己造 `_Frame`（见 `_train_frame`）★
        g.train_on = True
        g.train_seq = seq
        g.train_gaps = gaps
        # ★ 摆在谱面窗右边 ★
        #   用户：「点这个旁边会直接显示另外一个铺面」——
        #   "旁边"就是右边；尺寸跟谱面窗一样，两个并排看着齐。
        self.train.setGeometry(self.overlay.x() + self.overlay.width() + 12,
                               self.overlay.y(),
                               self.overlay.width(),
                               self.overlay.height())
        self.train.set_song_name(self.overlay.lbl_song.text())
        self.train.show()
        self._train_refresh()
        # ★ 不再起那个 16ms 的定时器 ★
        #   它原来是为了让收缩圈动起来。用户要的是"不用倒计时、
        #   只需要显示就行" —— 圈没了，就不需要按帧重绘了。
        #   训练面板现在只在**点对/点错**的时候重绘一次，
        #   平时一个像素都不动，也就不会白烧 CPU。

    def _train_tick_stop(self):
        try:
            self.train.grid._train_tick_t.stop()
        except Exception:
            pass

    def _train_refresh(self):
        """把训练进度刷到面板上。

        ★ 这里为什么只剩几行 ★
          第一版是控制台把"目标格 / 还要连点几下 / 接下来几个在哪"
          全算好再喂过去。§16.70 之后训练面板走**跟谱面窗同一套绘制**
          （`_paint_cells` / `_paint_badges` 那套），它自己就能从 `_Frame`
          里算出序号角标和 `×N` —— 再喂一遍等于把同一件事算两遍，
          两边还可能算出不一样的结果。
          所以现在只交代两件事：**练到第几个** + **这个音的间隔**
          （后者是给圈用的，就是用户要的"标记间隔时间"）。
        """
        n = len(self._train_seq)
        i = self._train_i
        g = self.train.grid
        if i >= n:
            g.train_seq = []
            self.train.set_target(None, '')
            self.statusBar().showMessage('训练：练完了（一共 %d 个音）' % n)
            self._train_tick_stop()
            return
        gap = self._train_gaps[i] if i < len(self._train_gaps) else 0.5
        g.train_text = '%d / %d' % (i + 1, n)
        g.train_start(i, gap)             # 从这一刻重新起算（圈开始缩）
        g.update()
        self.statusBar().showMessage(
            '训练：第 %d / %d 个 —— %s（这个音到下个隔 %.2f 秒）'
            % (i + 1, n, self._train_seq[i], max(0.15, gap)))

    def _on_train_pad(self, pitch: str):
        """在训练面板上点了一格 —— 对就前进，错就停在原地。

        用户：「点按要有声音」+「要和示谱器一样」——
        制谱器那个打击垫就是"点一下出一个音"，训练面板照办：
        **点什么都响**（点错的也响），不用先去开「播放声音」。
        自己听得见弹的是什么，才谈得上"练"。

        ★ 点错为什么"不动"，而不是"跳过" ★
          训练的全部意义就是"点对当前这个"。允许错着往下走的话，
          它跟随便乱点就没区别了，什么也练不出来。
          给一条 toast 说清该按哪个，然后停在这儿等你点对。
        """
        if not self.overlay.handle.btn_train.isChecked():
            return
        # ★ 先出声 —— 不管对错 ★
        #   跟 `_on_overlay_pad` 走同一套音源（`NotePlayer`），
        #   所以音色跟打击垫、跟「播放声音」完全一致。
        if self._ensure_notes():
            try:
                self._notes.play(pitch)
            except Exception:
                pass
        n = len(self._train_seq)
        i = self._train_i
        if i >= n:
            return
        want = self._train_seq[i]
        if pitch == want:
            self._train_i += 1
            # 点对了让那一格自己闪一下（跟打击垫同一套反馈）
            self.train.grid.set_flash(pitch, 0.25, min_gap=0.02)
            self._train_refresh()
            if self._train_i >= n:
                self.overlay.show_toast('训练完成：一共 %d 个音' % n, 2.5)
        else:
            self.overlay.show_toast('不是这个 —— 该按 %s' % want, 1.2)
        # 用鼠标点一下要花时间，点完这一下之后"接下来几个"就变了，
        # 主动重绘一次训练面板，别等下一帧。
        self.train.grid.update()

    def _pick_sheet_menu(self):
        """浮窗上点「选曲」—— 弹一个菜单列出 `sheets` 里的谱子。"""
        paths = all_sheets()
        if not paths:
            self.statusBar().showMessage('sheets 文件夹里还没有谱面')
            return
        menu = QMenu(self)
        for p in paths:
            act = menu.addAction(os.path.basename(p))
            act.setData(p)
        picked = menu.exec(QCursor.pos())
        if picked is not None and picked.data():
            self.statusBar().showMessage('载入 %s' % os.path.basename(picked.data()))
            self.load_sheet(picked.data())

    def _on_tick(self, sec: float):
        if not self.tl:
            return
        total = max(0.001, self.tl.total_sec)
        if not self.sld_prog.isSliderDown():
            self.sld_prog.setValue(int(sec / total * 1000))
        # 浮窗那条进度也跟上（它自己不判断"正在拖"—— 由那边管）
        self.overlay.set_progress(sec, self.tl.total_sec)
        idx = self.tl.index_at(sec)
        self.lbl_time.setText(
            '%s / %s　·　第 %d / %d 个音'
            % (_fmt_time(sec), _fmt_time(self.tl.total_sec),
               max(0, idx + 1), len(self.tl)))

    def _on_state(self, playing: bool):
        self.btn_play.setText('⏸  暂停' if playing else '▶  播放')
        # 浮窗顶上那颗按钮也跟着切 ▶ / ⏸
        self.overlay.set_playing(playing)

    # ------------------------------------------------------------------
    # 游戏内快捷键
    # ------------------------------------------------------------------

    def _apply_hotkeys(self):
        """按各个绑定框当前的值重新注册全局热键。"""
        pairs = ((HK_PLAYPAUSE, self.hk_play, '开始/暂停'),
                 (HK_RESTART, self.hk_restart, '从头开始'),
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

        ★ 隐身**不是** `windowOpacity = 0` ★
          实现是 `overlay.set_ghost(True)` —— 窗口还在、照样收全局热键，
          只是**什么都不画**（窗口本身是 WA_TranslucentBackground，
          不画就等于全透明）。见 `overlay.py` 的 `set_ghost()`。
          别改回 `setWindowOpacity(0)`：它会动 WS_EX_LAYERED，跟鼠标穿透
          的样式管理打架 —— 实测勾上「允许拖动」后浮窗会变成一片白。
          也别用 `hide()`：隐藏掉的窗口收不到 WM_HOTKEY，
          一隐身就再也按热键唤不回来了。
        """
        # （「谱面窗顶上显示拖动手柄」那个开关已经删了 —— 手柄**常驻**。
        #   浮窗整体关掉 / 临时隐身时，`_update_handle_visibility()`
        #   会按 `isVisible()` / `_ghost` 自己把它收起来。）
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
        # 把「浮窗现在是露着还是隐身」写清楚 —— 不然会以为它坏了
        if self.overlay.ghost:
            state = '浮窗已隐身（不在卡丘前台）'
        elif not self.chk_overlay.isChecked():
            state = '浮窗已关闭'
        else:
            state = '浮窗显示中'
        txt = '%s　%s　→　%s' % ('🎮 卡丘在前台' if in_game else '…不是卡丘',
                                winfocus.describe_foreground(), state)
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
        """从开头播放：先停下，倒数若干秒，再从 0 秒开播。"""
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

    # ------------------------------------------------------------------
    # 实时跟弹 / 录音（共用同一条 loopback 流）
    # ------------------------------------------------------------------

    def _toggle_follow_sheet(self):
        """📜 跟谱面：浮窗按**谱面里的时间**自动往前走，你跟着它弹。

        用户：「不能跟随已经录制好了的谱子来走吗，**这不是有样本**」。

        对 —— 谱面里每个音都有准确的时间（就是那个"样本"），
        所以这条路**零延迟、不可能错**，速度也是定死的。

        实测（`tools/diag_latency.py`）：时钟和浮窗落后 **0.0000 秒**。
        之前用户觉得"延迟"，是因为他弹得比谱面快 ——
        那是**跟谱面**和**跟手**的区别，不是显示慢。

        这条路跟下面的「▶ 播放」是同一件事（都走 `self.player`），
        只是入口放在谱面区，跟「🎵 跟手」并排，一眼能看出有两个选择。
        """
        if self.player.playing:
            self.player.pause()
            self.btn_live.setText('📜 跟谱面')
            self.lbl_live.setText('跟谱面：已停')
            self.statusBar().showMessage('跟谱面已停')
            return
        if not (self.tl and self.tl.items):
            self.statusBar().showMessage('还没有载入谱面')
            return
        # ★ 切到跟谱面时要**真的**把推进器扔掉 ★
        #   跟谱面是时钟在推浮窗。要是还留着制谱器试听建的那个推进器，
        #   「↶ 上一个」照样会去动 `set_time` —— 两个来源抢同一个位置，
        #   画面来回跳。
        self._follow = None
        self.overlay.set_mark_current(True)
        self.overlay.clear_flash()
        self.player.seek(0.0)
        self.player.play()
        self.btn_live.setText('⏹ 停止跟谱面')
        self.lbl_live.setText('跟谱面：按谱面时间走 —— 你跟着它弹')
        self.statusBar().showMessage('跟谱面中 —— 浮窗会按谱面时间依次亮键')

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
    # 悬浮窗几何
    # ------------------------------------------------------------------

    def _apply_geometry(self):
        self.overlay.setGeometry(self.sp_x.value(), self.sp_y.value(),
                                 self.sp_w.value(), self.sp_h.value())
        self._sync_size_label()
        self._save_config()

    # ---- 大小滑块 ----

    # 100% 对应的基准尺寸（浮窗出厂大小）
    BASE_W, BASE_H = 470, 580

    def _sync_size_label(self):
        """把「大小」滑块和标签对齐到当前实际的宽高。"""
        w, h = self.sp_w.value(), self.sp_h.value()
        pct = int(round(w * 100.0 / self.BASE_W))
        self.sld_size.blockSignals(True)
        self.sld_size.setValue(max(self.sld_size.minimum(),
                                   min(self.sld_size.maximum(), pct)))
        self.sld_size.blockSignals(False)
        self.lbl_size.setText('%d%%　%d×%d' % (pct, w, h))

    def _on_size_changed(self, pct: int):
        """等比缩放浮窗 —— 左上角不动，只改宽高。

        用户把 X/Y/宽/高 四个数字框清掉之后，调大小就靠这一个滑块：
        填四个数字太难用，而且很容易把浮窗拉成奇怪的长宽比。
        """
        w = max(180, int(round(self.BASE_W * pct / 100.0)))
        h = max(180, int(round(self.BASE_H * pct / 100.0)))
        self.sp_w.setValue(w)        # 这两个各自会触发 _apply_geometry
        self.sp_h.setValue(h)
        self.lbl_size.setText('%d%%　%d×%d' % (pct, w, h))

    # ★ `_on_drag_toggled()` 删掉了 ★
    #   它服务的那个「允许拖动（临时取消鼠标穿透）」勾选框已经没了
    #   （用户：「这个选项也没有用了」）。浮窗现在**始终**穿透，
    #   要挪位置就拖顶上那条「⠿」手柄 —— 只有它不穿透。

    # ---- 把手（抓手）----

    # （`_on_handle_toggled()` 已删除 —— 用户：「这个选项可以去掉，
    #   那个地方需要经常显示的」。手柄现在**常驻**，没有开关可接。）

    def _on_handle_drag_start(self):
        """开始拖把手 —— **不需要记任何状态**。

        松手时 `_on_handle_drag_finish` 直接读浮窗的实际几何去存位置，
        所以以前那个 `_handle_dragging` 标志全项目只写不读，已经删掉。
        槽本身留着：`overlay.handle.drag_started` 还连着它。
        """

    def _on_handle_drag_finish(self):
        g = self.overlay.geometry()
        self._sync_geom_from_overlay()          # 顺手把新位置存进配置
        self.statusBar().showMessage(
            '谱面窗已挪到 (%d, %d)' % (g.x(), g.y()))

    def _sync_geom_from_overlay(self):
        g = self.overlay.geometry()
        for sp, val in ((self.sp_x, g.x()), (self.sp_y, g.y()),
                        (self.sp_w, g.width()), (self.sp_h, g.height())):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        self._sync_size_label()
        self._save_config()

    # ★ `_snap_corner()` 删掉了 ★
    #   它服务的「贴到屏幕右上角」按钮已经没了（用户：「这个特可以去掉」）。
    #   右上角也不是什么特殊位置 —— 拖着手柄过去就是了。

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _load_config(self):
        """读配置并灌进各个控件。

        ★ 读盘和"填控件"分成两层了 ★
          文件 ⇄ dict 那一层在 `ui/config.py`（纯函数、可单测）；
          这里只负责"dict 的每个键对应哪个控件"。

        ★ 灌值期间必须把信号掐掉 ★
          `sp_x/sp_y/sp_w/sp_h` 的 `valueChanged` 连的是 `_apply_geometry`，
          而 `_apply_geometry` 末尾会 `_save_config` —— 于是每设一个值
          就写一次盘，**而且那时其它控件还没初始化完，写进去的是半成品配置**。
          掐掉之后，末尾统一 `_apply_geometry()` 一次就够。
        """
        cfg = config.load()
        self._cfg = cfg

        spinners = (self.sp_x, self.sp_y, self.sp_w, self.sp_h)
        for w in spinners:
            w.blockSignals(True)
        try:
            for sp, key in zip(spinners, ('x', 'y', 'w', 'h')):
                sp.setValue(int(cfg[key]))
        finally:
            for w in spinners:
                w.blockSignals(False)

        self.sld_opacity.setValue(int(cfg['opacity']))
        self.sld_bg.setValue(int(cfg['bg_alpha']))
        self.sp_preview.setValue(int(cfg['preview']))
        self.lbl_opacity.setText('%d%%' % self.sld_opacity.value())
        self.lbl_bg.setText('%d%%' % self.sld_bg.value())
        self.overlay.set_user_opacity(self.sld_opacity.value() / 100.0)

        self.sp_count.setValue(int(cfg['countdown']))
        for ed, key in ((self.hk_play, 'hk_play'),
                        (self.hk_restart, 'hk_restart'),
                        (self.hk_toggle, 'hk_toggle')):
            ed.set_binding(*parse_binding(cfg.get(key, '不绑定')))

        for chk, key in ((self.chk_overlay, 'overlay_show'),
                         (self.chk_only_game, 'only_game'),
                         (self.chk_follow_editor, 'follow_editor'),
                         (self.chk_sound, 'sound')):
            chk.blockSignals(True)
            chk.setChecked(bool(cfg[key]))
            chk.blockSignals(False)

        last = cfg.get('last_sheet')
        if not (last and os.path.isfile(last)):
            demo = os.path.join(sheets_dir(), 'demo.txt')
            last = demo if os.path.isfile(demo) else None
        if last:
            self.load_sheet(last)
        self._apply_geometry()
        self._apply_view_options()
        self._apply_overlay_visibility()
        # ★ 「播放声音」上次是开着的，这时候才真的接上 ★
        #   音源要这时才载入；载不出来 `_set_sound` 会自己把开关弹回去。
        if cfg['sound']:
            self._set_sound(True)

    def _save_config(self):
        """把当前界面状态存回配置文件。

        字段名集中在 `ui/config.py` 的 `DEFAULTS` 里定义，
        这里只做"控件 → dict"的映射。
        """
        config.save({
            'x': self.sp_x.value(), 'y': self.sp_y.value(),
            'w': self.sp_w.value(), 'h': self.sp_h.value(),
            'opacity': self.sld_opacity.value(),
            'bg_alpha': self.sld_bg.value(),
            'preview': self.sp_preview.value(),
            'last_sheet': self.path,
            'hk_play': [self.hk_play.mods, self.hk_play.vk],
            'hk_restart': [self.hk_restart.mods, self.hk_restart.vk],
            'hk_toggle': [self.hk_toggle.mods, self.hk_toggle.vk],
            'overlay_show': self.chk_overlay.isChecked(),
            'follow_editor': self.chk_follow_editor.isChecked(),
            'sound': self.chk_sound.isChecked(),
            'only_game': self.chk_only_game.isChecked(),
            'countdown': self.sp_count.value(),
        })

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
        self.overlay.hide()
        self.overlay.close()
        event.accept()
        # ★ 这里**不能**再调 `self.close()` ★
        #   Qt 的 `QWidget::close()` 在窗口仍然可见时会**再投递一次
        #   QCloseEvent**（窗口的隐藏发生在 closeEvent 返回之后），
        #   于是 unbind_all / _save_config / overlay.close()
        #   会整条再跑一遍，一路递归下去能到 RecursionError。
        #   `event.accept()` + `QApplication.quit()` 已经够了。
        QApplication.quit()
