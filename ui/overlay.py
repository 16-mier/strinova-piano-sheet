# -*- coding: utf-8 -*-
"""谱面悬浮窗 + 播放时钟。

★ 三条铁律（都跟游戏体验有关，别改坏）★
 1. 置顶 —— 必须压在游戏画面上
 2. 鼠标完全穿透 —— 枪的准星、点击都要穿过去，绝不能挡住
 3. **绝不抢焦点** —— 一旦抢了焦点，游戏会失焦 → UE4 会解除鼠标锁定，
    表现就是「鼠标飘出游戏窗口」。所以 WA_ShowWithoutActivating +
    WindowDoesNotAcceptFocus + WS_EX_NOACTIVATE 三管齐下。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wintypes
import math
import time

from PyQt6.QtCore import (QElapsedTimer, QObject, QPoint, QPointF, QRect,
                          QRectF, QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QVBoxLayout, QWidget)

from core.timeline import Timeline

from . import appstyle
from . import theme as T
from .views import GridView


class _MSG(ctypes.Structure):
    """Win32 MSG（nativeEvent 里拿到的是它的指针）。"""

    _fields_ = [('hwnd', _wintypes.HWND),
                ('message', _wintypes.UINT),
                ('wParam', _wintypes.WPARAM),
                ('lParam', _wintypes.LPARAM),
                ('time', _wintypes.DWORD),
                ('pt', _wintypes.POINT)]

# ---- Win32 常量（用于运行时切换鼠标穿透）----
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

# SetWindowPos 的选项位
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.SetWindowPos.restype = ctypes.c_bool

HWND_TOP = 0

# ★ 34（原来是 18，中间试过 24）★
#   它现在不只是一条"抓手"了 —— 还是一条**演奏控制条**：
#   选曲 / 进度 / 播放暂停。24px 放不下按钮和滑块。
HANDLE_H = 34
# ★ 曲名那一行 ★ —— 用户：「下面要显示哪首」
#   「下面」= 控制条下面。它是**单独一行**，不是挤在控制条里 ——
#   浮窗默认才 470 px 宽，控制条上一行还要放拖动 / 选曲 / 播放 /
#   进度 / 声音五个东西，再塞曲名只会把每个都挤成一条缝。
#   26 px 是 13 号字 + 一点上下留白，再矮就顶到字了。
SONG_H = 26

# ----------------------------------------------------------------------
# ★ 透视贴合的尺寸常量：已删除 ★
# ----------------------------------------------------------------------
#
# 这里原来有一组 `FIT_PAD` / `FIT_HEAD` / `FIT_FOOT` / `FIT_MIN_W` /
# `FIT_MIN_H` / `CORNER_R` / `GRAB_R` / `SPLIT_R` / `SPLIT_GRAB_R` ——
# 都是"贴合模式"专用的：浮窗要摆成罩住四边形、上下多留一截给被外推的
# 大字和红框、四个角把手和六个分格线把手的半径等等。
#
# 贴合整套删掉之后（用户：「自动贴合也删掉，可以调整大小和位置就行了」），
# 浮窗就是一个普普通通的固定窗口，位置和大小由控制台的滑块 / 手柄决定。


def _make_noactivate(widget):
    """给窗口补上「绝不抢焦点」的扩展样式。

    `Qt.WindowType.WindowDoesNotAcceptFocus` 在 Windows 上本来就该映射成
    `WS_EX_NOACTIVATE`，但那一步依赖 Qt 的平台插件时机 —— 窗口句柄
    刚建出来的时候未必已经生效。拖把手时如果真把它激活了，
    游戏就会失焦 → UE4 解除鼠标锁定（就是那个「鼠标飘出窗口」的老毛病）。
    所以这里显式再补一次，`SWP_FRAMECHANGED` 让系统重算。
    """
    try:
        hwnd = int(widget.winId())
        ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                    GWL_EXSTYLE) & 0xFFFFFFFF
        ex |= (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED)
        _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)
        _user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                             | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except Exception:
        pass


def _ex_style(widget) -> int:
    """窗口当前的扩展样式（诊断 / 测试用）。"""
    try:
        return _user32.GetWindowLongW(
            ctypes.c_void_p(int(widget.winId())), GWL_EXSTYLE) & 0xFFFFFFFF
    except Exception:
        return 0


def _raise_noactivate(widget):
    """把窗口提到最前，但<b>不激活</b>它。"""
    try:
        _user32.SetWindowPos(ctypes.c_void_p(int(widget.winId())), HWND_TOP,
                             0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


class Player(QObject):
    """播放时钟 —— 用真实经过的时间推进，不受界面卡顿影响。

    ★ 它还会「到点出声」★

      用户：「应该做成和音乐播放器一样的，然后多一个选项，播放声音」。
      在这之前这条路是**纯时钟**：点 ▶ 只有格子依次亮过去，一点声音都没有 ——
      而控制台上那句说明写的是「适合先听几遍熟悉节奏」，没有声音根本听不了。

      发声本身不在这个类里：`self.sound` 由控制台注入
      （`ui/keypad.py` 那个 `NotePlayer`，跟打击垫共用一套音源和音色），
      这里只负责"什么时候该敲哪个键"。

    ★ 开关是 `sound_on`，跟 `sound` 分开 ★
      `sound` 是"音源在不在"（懒载入，打开开关才建），
      `sound_on` 是"用户要不要听"。两个都成立才真的出声 ——
      这样关掉开关不用把音源拆掉，再打开是瞬间的事。
    """

    tick = pyqtSignal(float)
    state_changed = pyqtSignal(bool)      # True=正在播
    # ★ 落后超过这个秒数的音**不补** ★
    #   拖动进度条、或者系统卡了一下之后，`sec` 可能一下跳过去好几个音。
    #   全补出来是一串糊在一起的噪音，听着就像程序出错。
    SOUND_LATE = 0.25

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.sec = 0.0
        self.playing = False
        # ---- 播放声音 ----
        self.sound = None            # 由控制台注入的 `NotePlayer`
        self.sound_on = False        # 用户那个「播放声音」开关
        # ★ 「跟打」：一个音都不放，全等你点 ★
        #   它跟 `sound_on` 是**两件事**，故意分开：
        #   `sound_on` 是用户的设置（要存进配置），
        #   `karaoke` 是这一会儿的模式（不存）。合成一个的话，
        #   练一次琴就会把他「播放声音」的设置改掉。
        self.karaoke = False
        self._ptr = 0                # 下一个还没发声的音在 `items` 里的下标
        self._base = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(8)                       # ~120fps，快速连音也要跟得上
        self._timer.timeout.connect(self._on_tick)

    # ---- 控制 ----

    def set_timeline(self, tl: Timeline | None):
        self.stop()
        self.timeline = tl

    def play(self):
        if not self.timeline or not self.timeline.items:
            return
        if self.playing:
            return
        if self.sec >= self.timeline.total_sec:
            self.sec = 0.0
        self._base = self.sec
        self._sync_ptr()          # ★ 从这儿往后数 —— 前面那些不补 ★
        self._clock.restart()
        self.playing = True
        self._timer.start()
        # ★ 起点上那个音立刻响，不等第一跳 ★
        #   第一跳在 8 ms 之后，落到 `sec` 上已经大了一点点；要是等它，
        #   "从 0 秒开始"的第一个音就会晚一帧才出来。这里先补一发，
        #   位置还没动，响的正好是 `sec` 上该响的那个。
        self._fire_sound()
        self.state_changed.emit(True)

    def pause(self):
        if not self.playing:
            return
        self._timer.stop()
        self.playing = False
        self.state_changed.emit(False)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def stop(self):
        self._timer.stop()
        was = self.playing
        self.playing = False
        self.sec = 0.0
        self._base = 0.0
        self._ptr = 0
        self.tick.emit(self.sec)
        if was:
            self.state_changed.emit(False)

    def seek(self, sec: float):
        total = self.timeline.total_sec if self.timeline else 0.0
        self.sec = max(0.0, min(sec, total))
        self._base = self.sec
        self._sync_ptr()          # 拖到哪儿就从哪儿接着响，别把中间的全倒出来
        if self.playing:
            self._clock.restart()
        self.tick.emit(self.sec)

    def nudge(self, delta: float):
        """微调播放位置（逐格排查用）。"""
        self.seek(self.sec + delta)

    # ---- 出声 ----

    def set_sound_on(self, on: bool):
        """「播放声音」开关。

        ★ 打开的那一下要把指针推到当前位置 ★
          不然它是从曲子开头往后数的 —— 开了之后会把前面
          几十个音当成"欠着的"一口气全放出来。
        """
        self.sound_on = bool(on) and self.sound is not None
        if self.sound_on:
            self._sync_ptr()

    def _sync_ptr(self):
        """把指针挪到"当前时刻之后第一个还没发声的音"上。"""
        if not self.timeline:
            self._ptr = 0
            return
        items = self.timeline.items
        limit = self.sec - 1e-6
        i = 0
        while i < len(items) and items[i].start_sec < limit:
            i += 1
        self._ptr = i

    def _fire_sound(self):
        """把这一跳之前该响的音放出来。

        ★ 为什么用指针而不是每次遍历整条时间轴 ★
          8 ms 一跳、曲子几百上千个音，每跳都从头扫一遍纯属白烧 CPU。
          指针只往前走，追上了就什么都不做。
        """
        if not (self.sound_on and self.sound is not None and self.timeline):
            return
        items = self.timeline.items
        sec = self.sec
        while self._ptr < len(items) and items[self._ptr].start_sec <= sec:
            it = items[self._ptr]
            self._ptr += 1
            # ★ 「跟打」：原声一个都不放 —— 但指针照样往前推 ★
            #   不推的话，关掉跟打那一刻会把前面几十个音当成
            #   "欠着的"一口气放出来（跟 `SOUND_LATE` 要治的是同一个病）。
            if self.karaoke:
                continue
            if sec - it.start_sec > self.SOUND_LATE:
                continue              # 迟到的就不响了（见 SOUND_LATE）
            for pitch in it.chord.pitches:
                try:
                    self.sound.play(pitch)
                except Exception:
                    pass

    # ---- 内部 ----

    def _on_tick(self):
        if not self.timeline:
            return
        self.sec = self._base + self._clock.elapsed() / 1000.0
        if self.sec >= self.timeline.total_sec:
            self.sec = self.timeline.total_sec
            self._fire_sound()
            self.tick.emit(self.sec)
            self.pause()
            return
        self._fire_sound()
        self.tick.emit(self.sec)


class DragHandle(QWidget):
    """谱面窗的「把手」—— 一个**独立的小窗口**，永远抓得住。

    ★ 用户报的「这个显示谱面的窗口无法移动」就是缺了它 ★
      谱面窗本体带着 `WS_EX_TRANSPARENT`（鼠标完全穿透，枪的准星能打过去），
      而这是个**窗口级**的样式 —— 没法只让"顶上一条"接收鼠标。
      想用 `WM_NCHITTEST` 返回 `HTTRANSPARENT` 来做到"这里收、那里穿"也不行：
      文档里写死了它只在**同一个线程**的窗口之间生效，
      游戏在另一个进程 / 另一个线程，点了照样传不过去。
      于是只剩一条路：**再开一个不穿透的小窗口专门当把手**。

    于是现在的交互是：
      · 想挪谱面窗 —— 直接按住顶上那条"⠿"拖，**什么都不用先去勾**
      · 控制台里的「允许拖动」还在，勾上之后整个浮窗都能拖（粗调更方便）

    ★ 它绝不抢焦点 ★
      `WindowDoesNotAcceptFocus` + 显式补 `WS_EX_NOACTIVATE`：
      拖它的时候游戏不会失焦，UE4 也就不会解除鼠标锁定。
    """

    drag_started = pyqtSignal()
    drag_moved = pyqtSignal(int, int)        # 拖到 (x, y)
    drag_finished = pyqtSignal()
    # ★ 控制条那四个控件 ★
    #   它自己**不认识**播放器和曲库 —— 只发信号，由控制台去接
    #   （跟 `player.tick → overlay.set_time` 是同一条路子）。
    #   浮窗不需要知道"曲子从哪来""谁在放"。
    play_toggled = pyqtSignal()              # 点「启动 / 暂停」
    seek_requested = pyqtSignal(float)       # 进度拖到某处（0~1 的比例）
    pick_requested = pyqtSignal()            # 点「选曲」
    sound_toggled = pyqtSignal(bool)         # 点「播放声音」开关（要/不要）
    # ★ 「跟打」和「可按」★ —— 用户：「新增一个跟打和激活为可点按发声的按钮」
    karaoke_toggled = pyqtSignal(bool)       # 跟打：不放原声，改成你点出声
    pad_click_toggled = pyqtSignal(bool)     # 可按：让浮窗的格子能被点
    train_toggled = pyqtSignal(bool)         # 训练：按音符顺序一个一个点

    def __init__(self, owner: QWidget):
        super().__init__(None)
        self._owner = owner
        self._off = None            # 按下点相对把手左上角的偏移
        self._hot = False
        self._last = None
        self.setWindowTitle('卡丘琴谱器 · 把手')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                     # 不占任务栏
            | Qt.WindowType.WindowDoesNotAcceptFocus  # ★ 不抢焦点
        )
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setMouseTracking(True)
        self.setToolTip('空白处按住可以拖动谱面窗（不会抢游戏的焦点）')
        self.resize(460, HANDLE_H)
        self._seeking = False
        self._build_bar()

    # ---- 控制条 ----

    def _build_bar(self):
        """摆上「选曲 / 启动暂停 / 进度 / 播放声音」。

        ★ 排成音乐播放器的样子 ★

            拖动   选曲   播放   进度──────────   声音

          用户：「应该做成和音乐播放器一样的，然后多一个选项，播放声音」。
          以前这条上只有「选曲 / 进度 / 启动暂停」，而且**一点声音都没有** ——
          点 ▶ 只有格子依次亮过去，想"先听几遍熟悉节奏"根本听不着。

        ★ 拖动没被挤掉 ★
          子控件会先吃掉鼠标事件 —— 所以这条上**空白的地方**照样能按住
          拖窗口，左边那个 `⠿` 就是专门留出来的拖动区。
        """
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)

        grip = QLabel('⠿')
        grip.setToolTip('按住这里拖动谱面窗')
        grip.setStyleSheet('color:#bcd8ff;background:transparent;')
        lay.addWidget(grip)

        # ★ 这三颗按钮都得挂 `bar` 这个 objectName ★
        #   它们只有 28×22，而通用 `QPushButton` 那条带着
        #   `padding: 5px 14px` + `min-height: 20px` —— 直接套上去，
        #   内容区宽度会变成负的，`setText()` 的字一个都显示不出来
        #   （用户真机截图：「这俩按键都没显示出来啊」）。
        #   `QPushButton#bar` 那一条把它们归零，见 `ui/appstyle.py`。
        #
        # ★ 图标全部自己画，不用 📚 / ▶ / ⏸ 这些字符 ★
        #   真机上它们会被系统换成**彩色 emoji**（一本彩色的书、
        #   一个蓝色小方块），压在深色控制条上很跳，跟喇叭也不搭。
        #   见 `appstyle.bar_icon`。
        self.btn_pick = QPushButton()
        self.btn_pick.setObjectName('bar')
        self.btn_pick.setFixedSize(28, HANDLE_H - 12)
        self.btn_pick.setIconSize(QSize(15, 15))
        self.btn_pick.setIcon(QIcon(appstyle.bar_icon('list')))
        self.btn_pick.setToolTip('选曲 —— 换一份谱面')
        # ★ 用 lambda 包一层 ★ —— `clicked` 会带一个 `checked` 过来，
        #   直接接 `emit()`（不收参数）会报 TypeError。
        self.btn_pick.clicked.connect(lambda *_: self.pick_requested.emit())
        lay.addWidget(self.btn_pick)

        self.btn_play = QPushButton()
        self.btn_play.setObjectName('bar')
        self.btn_play.setFixedSize(28, HANDLE_H - 12)
        self.btn_play.setIconSize(QSize(15, 15))
        self.btn_play.setIcon(QIcon(appstyle.bar_icon('play')))
        self.btn_play.setToolTip('播放')
        self.btn_play.clicked.connect(lambda *_: self.play_toggled.emit())
        lay.addWidget(self.btn_play)

        self.sld = QSlider(Qt.Orientation.Horizontal)
        self.sld.setRange(0, 1000)
        self.sld.setToolTip('播放位置 —— 按住拖')
        self.sld.sliderPressed.connect(self._on_seek_start)
        self.sld.sliderReleased.connect(self._on_seek_end)
        lay.addWidget(self.sld, 1)

        # ★ 播放声音 ★
        #   用一个可切换的按钮而不是勾选框：这条只有 34 px 高，
        #   塞得下一个图标，塞不下「播放声音」四个字。
        #   图标本身就是状态（喇叭带 × = 关 / 喇叭带声波 = 开），
        #   不用再去看勾没勾。
        #
        #   ★ 图标是自己画的 PNG，不是 🔊 / 🔇 ★
        #     那两个字符要系统里有彩色 emoji 字体才有字形，缺了就是空心方块，
        #     而且看不出"是没显示还是没开"。见 `appstyle._speaker_icon`。
        self.btn_sound = QPushButton()
        self.btn_sound.setObjectName('bar')
        self.btn_sound.setFixedSize(28, HANDLE_H - 12)
        self.btn_sound.setCheckable(True)
        self.btn_sound.setChecked(False)
        self.btn_sound.setIconSize(QSize(15, 15))
        self.btn_sound.setToolTip('播放声音：关着（点一下打开）')
        self.btn_sound.toggled.connect(self._on_sound_toggled)
        lay.addWidget(self.btn_sound)
        self.set_sound(False)

        # ★ 「跟打」★
        #   播放的时候**不放原声**，改成你点格子出声 —— 练琴用：
        #   听着自己弹的，才听得出来哪儿按错了。
        #   名字就用两个汉字：这条上已经五个控件了，
        #   再画图标没人认得出哪个是哪个。
        self.btn_karaoke = QPushButton('跟打')
        self.btn_karaoke.setObjectName('bar')
        self.btn_karaoke.setFixedSize(40, HANDLE_H - 12)
        self.btn_karaoke.setCheckable(True)
        self.btn_karaoke.setChecked(False)
        self.btn_karaoke.setToolTip('跟打：关着（点一下打开）')
        self.btn_karaoke.toggled.connect(self._on_karaoke_toggled)
        lay.addWidget(self.btn_karaoke)
        self.set_karaoke(False)

        # ★ 「可按」★ —— 让浮窗的格子**能被点**。
        #   浮窗平时是鼠标穿透的（枪的准星要能打过去），
        #   开着它鼠标就不再穿过去 —— 所以这是个临时状态，用完记得关。
        self.btn_tap = QPushButton('可按')
        # ★ objectName 就是 `bar`，跟「跟打」一样 ★
        #   这里走过一段弯路：用户先说「可按的时候不要显示高亮」，
        #   我理解成"这颗按钮勾上时别变色"，于是给它单开了个 `bar2`
        #   把 `:checked` 的底色压掉了 —— 结果用户回头说
        #   「可按这个按键没有变亮」，因为开着跟关着长得一模一样了。
        #   原来那个"高亮"根本不是 `:checked`，是 `:focus` 那条青绿边框
        #   （点一下按钮就拿焦点，边框一直挂着不走）—— 见
        #   `appstyle.py` 里 `QPushButton#bar:focus` 那段。
        #   所以它跟「跟打」一样用 `bar`：勾上就变青绿，一眼看得见。
        self.btn_tap.setObjectName('bar')
        self.btn_tap.setFixedSize(40, HANDLE_H - 12)
        self.btn_tap.setCheckable(True)
        self.btn_tap.setChecked(False)
        self.btn_tap.setToolTip('可按：关着（点一下打开）')
        self.btn_tap.toggled.connect(self._on_pad_click_toggled)
        lay.addWidget(self.btn_tap)
        self.set_pad_click(False)

        # ★ 「训练」★ —— 放在「跟打」旁边
        #   用户：「跟打功能旁边再增加一个训练功能，具体就是去点按键，
        #   但是不是按照曲子顺序来是按照音符顺序来，自己点，
        #   点一个继续下一个」。
        #
        #   跟「跟打」的区别一句话：跟打看**时间**（播放进度在跑），
        #   训练看**顺序**（第 1、2、3… 个音，点对当前这个才亮下一个）。
        #
        #   打开它会在谱面窗**旁边**多开一块 `TrainWindow` ——
        #   用户：「点这个旁边会直接显示另外一个铺面，
        #   之前那个铺面可以用来预览」。所以谱面窗照旧，不动它。
        self.btn_train = QPushButton('训练')
        self.btn_train.setObjectName('bar')
        self.btn_train.setFixedSize(40, HANDLE_H - 12)
        self.btn_train.setCheckable(True)
        self.btn_train.setChecked(False)
        self.btn_train.setToolTip('训练：关着（点一下打开）')
        self.btn_train.toggled.connect(self._on_train_toggled)
        lay.addWidget(self.btn_train)
        self.set_train(False)

        # ★ 控制条上的按钮一律不拿键盘焦点 ★
        #   用户连着报了两轮「可按的时候高亮没有消失」——
        #   那个"高亮"就是 `QPushButton:focus` 的青绿边框：
        #   点一下按钮，它就拿到焦点，那一圈边框挂在上面**不走**，
        #   而且跟"这个开关开着"长得几乎一样，把真正的 `:checked`
        #   底色盖掉了（用户原话：「高亮没有消失，然后可按这个按键
        #   没有变亮」—— 就是被它骗的）。
        #
        #   这条控制条是个 Tool 小窗（`WindowDoesNotAcceptFocus`），
        #   没有任何键盘交互，焦点落在这儿一点用都没有，只剩副作用。
        #   索性全设 `NoFocus`，从源头上就不拿。
        #   （`appstyle.py` 里还压了一道 `#bar:focus` 做保险，
        #     挡住程序化 `setFocus()` 之类的别的路径。）
        for _b in self.findChildren(QPushButton):
            _b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _on_sound_toggled(self, on: bool):
        self.set_sound(on)
        self.sound_toggled.emit(bool(on))

    def set_sound(self, on: bool):
        """「播放声音」开关的**显示** —— 由控制台决定它该是什么状态。

        ★ 必须掐掉信号 ★
          控制台那边接完信号会回头把状态同步回来（音源载不出来的话
          开关要弹回去），不掐掉就是"我设你、你设我"来回发。
        """
        on = bool(on)
        self.btn_sound.blockSignals(True)
        self.btn_sound.setChecked(on)
        self.btn_sound.blockSignals(False)
        self.btn_sound.setIcon(QIcon(appstyle.sound_icon(on, 15)))
        self.btn_sound.setToolTip('播放声音：开着（点一下关掉）' if on
                                  else '播放声音：关着（点一下打开）')

    def _on_karaoke_toggled(self, on: bool):
        self.set_karaoke(on)
        self.karaoke_toggled.emit(bool(on))

    def set_karaoke(self, on: bool):
        """「跟打」按钮的**显示** —— 状态由控制台拍板（它才管着播放器）。"""
        on = bool(on)
        self.btn_karaoke.blockSignals(True)
        self.btn_karaoke.setChecked(on)
        self.btn_karaoke.blockSignals(False)
        if on:
            self.btn_karaoke.setToolTip(
                '跟打：开着 —— 播放时不放原声，改成你点格子出声'
                '（点一下关掉）')
        else:
            self.btn_karaoke.setToolTip(
                '跟打：关着（点一下打开）\n'
                '打开之后播放不再自动出声，改成你点浮窗的格子发声')

    def _on_pad_click_toggled(self, on: bool):
        self.set_pad_click(on)
        self.pad_click_toggled.emit(bool(on))

    def set_pad_click(self, on: bool):
        """「可按」按钮的**显示** —— 穿透是窗口级的事，由控制台去调。"""
        on = bool(on)
        self.btn_tap.blockSignals(True)
        self.btn_tap.setChecked(on)
        self.btn_tap.blockSignals(False)
        if on:
            self.btn_tap.setToolTip(
                '可按：开着 —— 浮窗的格子能点着发声（点一下关掉）\n'
                '注意：开着的时候鼠标不再穿透浮窗，会挡住游戏画面')
        else:
            self.btn_tap.setToolTip(
                '可按：关着（点一下打开）\n'
                '打开之后浮窗的格子可以点，点一下就出那个音')

    def _on_train_toggled(self, on: bool):
        self.set_train(on)
        self.train_toggled.emit(bool(on))

    def set_train(self, on: bool):
        """「训练」按钮的**显示** —— 训练面板由控制台开（它管着谱面）。"""
        on = bool(on)
        self.btn_train.blockSignals(True)
        self.btn_train.setChecked(on)
        self.btn_train.blockSignals(False)
        if on:
            self.btn_train.setToolTip(
                '训练：开着 —— 谱面窗旁边那块面板按音符顺序一个个点\n'
                '（点对当前那个才亮下一个；点错了不动）\n'
                '点一下关掉')
        else:
            self.btn_train.setToolTip(
                '训练：关着（点一下打开）\n'
                '打开之后会在谱面窗旁边多开一块面板，\n'
                '按谱面里音符的**先后顺序**一个一个点 —— 不看时间，\n'
                '点对当前这个才亮下一个。谱面窗照旧当预览。')

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_end(self):
        self._seeking = False
        self.seek_requested.emit(self.sld.value() / 1000.0)

    def set_playing(self, on: bool):
        """播放状态变了 —— 按钮在 ▶ / ⏸ 之间切（两张图都是自己画的）。"""
        self.btn_play.setIcon(QIcon(appstyle.bar_icon('pause' if on else 'play')))
        self.btn_play.setToolTip('暂停' if on else '播放')

    def set_progress(self, sec: float, total: float):
        """更新进度。

        ★ 正在被拖的时候**不许覆盖** ★
          不然滑块会跟手指打架（播放器每 tick 一次就把它拽回去，
          手感就是"拖不动"）。
        """
        if self._seeking:
            return
        if total <= 1e-9:
            self.sld.setValue(0)
            return
        self.sld.setValue(int(max(0.0, min(1.0, sec / total)) * 1000))

    # ---- 外观 ----

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        # 平时几乎透明（不挡游戏画面），鼠标一上来就亮起来
        a = 0.85 if (self._hot or self._off is not None) else 0.30
        p.setPen(QPen(QColor(140, 200, 255, int(215 * a)), 1.2))
        p.setBrush(QBrush(QColor(10, 14, 22, int(220 * a))))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 5, 5)
        # ★ 不再画"按住这里拖动"那行字了 ★
        #   这条现在是**控制条**（选曲 / 进度 / 播放），子控件已经占满；
        #   再压一行说明上去只会跟按钮打架。拖动区靠左边那个 `⠿` 提示。

    def enterEvent(self, event):
        self._hot = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hot = False
        self.update()
        super().leaveEvent(event)

    # ---- 拖 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._off = (event.globalPosition().toPoint()
                         - self.frameGeometry().topLeft())
            self._last = None
            self.update()
            self.drag_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._off is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            p = event.globalPosition().toPoint() - self._off
            if p != self._last:
                self._last = p
                self.drag_moved.emit(p.x(), p.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._off is not None:
            self._off = None
            self._last = None
            self.update()
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        _make_noactivate(self)       # 句柄这时候才有效

    def ex_style(self) -> int:
        """当前扩展样式（诊断 / 测试用）。"""
        return _ex_style(self)




class TrainWindow(QWidget):
    """「训练」用的那块面板 —— **独立的一块窗**，跟谱面窗并排摆着。

    用户：「跟打功能旁边再增加一个训练功能……自己点，点一个继续下一个」
         「点这个旁边会直接显示另外一个铺面，之前那个铺面可以用来预览」

    ★ 为什么单开一块，而不是把谱面窗切过去 ★
      用户要的就是**两个同时看**：一边练（只亮当前这一个音），
      一边看谱面预览（该弹哪儿、后面还有几个音还看得见）。
      做成一窗口切换的话，练的时候就没得对照了。

    ★ 它跟谱面窗的区别只有两点 ★
      · 鼠标**永远不穿透** —— 训练本来就是拿来点的，
        不用先开「可按」那一步（那个开关存在的理由是"平时挡准星"，
        训练窗是临时开的，没这个问题）；
      · 画的是训练界面（见 `GridView._paint_train`），不是谱面提示。

    ★ 复用 `GridView`，不另写一套 ★
      16 个格子的几何、`_geom()`、`_cell_at()`、`pad_pressed` 信号
      全都是现成的 —— 训练和谱面共用的恰恰是"键位"这一层，
      不同的只是"画什么"。所以这里只把 `train_on` 打开。
    """

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowTitle('卡丘琴谱器 · 训练')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                     # 不占任务栏
            | Qt.WindowType.WindowDoesNotAcceptFocus  # 不抢焦点（别让游戏掉帧）
        )
        # ★ 注意这里**没有** `WA_TransparentForMouseEvents` ★
        #   谱面窗那边有个大坑：顶层窗口一旦设过那个属性，Qt 在窗口创建
        #   那一刻就把它固化成"输入穿透"，之后再清也回不来
        #   （见 `OverlayWindow.__init__` 那段）。训练窗压根不需要它 ——
        #   永远要能点，索性一行都不写。
        self.grid = GridView(self)
        self.grid.train_on = True
        self.grid.pad_click = True               # 点格子就发 `pad_pressed`
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.grid)
        self.resize(360, 360)

    # 转发一下，控制台只用跟这个窗口打交道
    @property
    def pad_pressed(self):
        return self.grid.pad_pressed

    def set_target(self, cell, text: str = ''):
        """换目标格 + 那行进度。"""
        self.grid.train_cell = cell
        self.grid.train_text = text
        self.grid.update()

    def set_song_name(self, text: str):
        self.setWindowTitle('卡丘琴谱器 · 训练　%s' % (text or ''))

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_exstyle()

    def _apply_exstyle(self):
        """补一个 `WS_EX_NOACTIVATE` —— 跟谱面窗一样的理由：
        训练的时候游戏不能掉焦点，掉了 UE4 就解除鼠标锁定。"""
        try:
            hwnd = int(self.winId())
            ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                        GWL_EXSTYLE) & 0xFFFFFFFF
            ex |= (WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            ex &= ~(WS_EX_TRANSPARENT & 0xFFFFFFFF)   # 确保不穿透
            _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)
            _user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                 | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        except Exception:
            pass


class OverlayWindow(QWidget):
    """置顶的谱面窗。"""

    def __init__(self, parent=None):
        super().__init__(None)
        self._click_through = True

        self.setWindowTitle('卡丘琴谱器')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # ★ 这里原来有一行 `setAttribute(WA_TransparentForMouseEvents)` ★
        #   —— 它就是「可按」按不动的**根因**，2026-09 拿 A/B 对照实验
        #   钉死的（`_diag_flags.py`）：
        #
        #     造两个除了这一点以外完全一样的窗口，
        #       A：show() 之前设 `WA_TransparentForMouseEvents`，
        #          show() 之后再 `setAttribute(..., False)` 清掉；
        #       B：从头到尾不设。
        #     两个窗口的 `testAttribute()` 读出来**都是 False**，
        #     但 `SendInput` 真点下去：B 的按钮响了，A 的一动不动 ——
        #     连窗口自己的 `mousePressEvent` 都不进。
        #
        #   Qt 在**窗口创建那一刻**就把这个属性变成了内部的
        #   "输入穿透"状态（`QWindow` 那层的 flag），之后再改 widget 上的
        #   属性，那个内部状态**不会跟着回来** —— `testAttribute()` 骗人，
        #   所以这个 bug 靠读属性永远查不出来。
        #   （Qt 6.11 / PyQt6。）
        #
        #   那为什么原来要设它？—— 以为"鼠标穿透"得靠它。
        #   **其实不用**：Windows 那边的 `WS_EX_TRANSPARENT`（见
        #   `_apply_exstyle`）已经让鼠标整窗穿过去了，实测穿透状态下
        #   `WindowFromPoint(浮窗上一点)` 返回的是**下面**那个窗口，
        #   浮窗连消息都收不到。所以这一条纯属多余，还顺手把
        #   "取消穿透"那条路堵死了。
        #
        #   ★ 结论：穿透**只**用 `WS_EX_TRANSPARENT`，不要碰这个 Qt 属性 ★
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                     # 不占任务栏
            | Qt.WindowType.WindowDoesNotAcceptFocus  # ★ 不抢焦点
        )

        self.grid_view = GridView(self)
        # ★ 曲名行 ★
        #   文字本身是透明的，底板由 `paintEvent` 画 ——
        #   这样它能跟着「底板浓度」一起淡下去（见 `paintEvent` 那段）。
        self.lbl_song = QLabel('还没有载入谱面', self)
        self.lbl_song.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_song.setFixedHeight(SONG_H)
        self.lbl_song.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction)   # 别被选中 / 别吃鼠标
        self.lbl_song.setStyleSheet(
            'QLabel{color:#dce6ff;background:transparent;font-size:13px;}')
        lay = QVBoxLayout(self)
        # ★ 顶上要留出把手那一条 ★
        #   把手是**独立的小窗口**，永远叠在谱面窗的 0~HANDLE_H 上。
        #   不留白的话曲名行会被它压住一半 —— 实测过：进度条和曲名
        #   挤在同一行上，两个都看不全。
        lay.setContentsMargins(0, HANDLE_H, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.lbl_song)
        lay.addWidget(self.grid_view, 1)

        # 倒计时大字（压在谱面上，倒数完自动开播）
        self.lbl_count = QLabel('', self)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setStyleSheet(
            'QLabel{color:#ffd230;font-size:72px;font-weight:bold;'
            'background:rgba(8,10,16,190);border-radius:20px;'
            'border:3px solid rgba(255,210,48,160);}')
        self.lbl_count.hide()

        # 小提示条（热键触发的状态反馈，不抢焦点）
        self.lbl_toast = QLabel('', self)
        self.lbl_toast.setWordWrap(True)
        self.lbl_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_toast.setStyleSheet(
            'QLabel{color:#e8ecf8;font-size:15px;'
            'background:rgba(8,10,16,205);border-radius:12px;'
            'border:2px solid rgba(120,200,255,150);padding:10px;}')
        self.lbl_toast.hide()

        # ★ 这三行字都不许吃鼠标 ★
        #   用户报「可按按不动」，除了上面那个穿透的根因，还有这一条：
        #   `show_toast()` 往谱面窗**底部中间**铺一片 400×120 的提示条
        #   （浮窗内 35,444 ~ 435,564 —— 正好压着下面两行格子）。
        #   它是 GridView 的**兄弟**控件，Qt 的命中测试会把它查出来，
        #   按下事件就给了它；它 `ignore()` 之后只会冒泡到父窗口，
        #   **永远不会转到 GridView** —— 于是那 2 秒里点格子完全没反应，
        #   toast 一消失又好了。这种"过一会儿自己就好"的毛病最难查。
        #
        #   实测证据（`_diag_reach.py`）：
        #     toast 挂着时  widgetAt(格子中心) = QLabel
        #     toast 过期后  widgetAt(格子中心) = GridView
        #   所以：装饰性的字一律 `WA_TransparentForMouseEvents`，
        #   让命中测试直接跳过它们。
        #
        #   ★ 跟构造函数里删掉的那个不是一回事 ★
        #     那个是设在**顶层窗口**上的（Qt 会固化、清不掉）；
        #     这里是**子控件**，Qt 老老实实按属性办事，
        #     而且我们只设 True、从不往回改，所以没有那个坑。
        for _w in (self.lbl_song, self.lbl_count, self.lbl_toast):
            _w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.lbl_toast.hide)

        self.hotkeys = None          # 由控制窗注入 HotkeyManager
        self._count_left = 0
        self._count_done = None
        self._count_timer = QTimer(self)
        self._count_timer.setInterval(1000)
        self._count_timer.timeout.connect(self._on_count_tick)

        self._drag_offset = None
        self._ghost = False          # 临时隐身（卡丘不在前台时）
        self._user_opacity = 1.0     # 用户在控制面板里设的不透明度
        # 实时跟弹高亮的刷新（有高亮才跑，平时零开销）
        self._flash_tick = QTimer(self)
        self._flash_tick.setInterval(8)
        self._flash_tick.timeout.connect(self._on_flash_tick)
        # 诊断用：最近闪过哪些键、什么时候闪的
        self._flog: list = []
        self._flog_t0 = time.monotonic()

        # ★ 把手（抓手）——「谱面窗无法移动」的解法 ★
        #   它是个**独立的顶层小窗口**，永远不穿透，所以随时抓得住。
        #   默认就露着：用户不该为了挪个窗口先去控制台勾一个复选框。
        self._handle_wanted = True
        self.handle = DragHandle(self)
        self.handle.drag_started.connect(self._on_handle_drag_start)
        self.handle.drag_moved.connect(self._on_handle_drag)
        self.handle.drag_finished.connect(self._on_handle_drag_end)

        # ★ 透视贴合：已删除 ★
        #   这里原来存着`_fit_quad_screen`（屏幕坐标的四角）、内部分界表、
        #   标定状态，以及一个铺满窗口的 `FitOverlay`（四个可拖的角 +
        #   六个内部分格线把手）。用户后来决定整套拿掉
        #   （「自动贴合也删掉，可以调整大小和位置就行了」）——
        #   浮窗回到固定摆放，位置靠「⠿」手柄拖、大小靠控制台的滑块。

        self.resize(470, 580)





















    # ---- 把手 ----

    def set_handle_visible(self, on: bool):
        """控制台里的「显示拖动手柄」。"""
        self._handle_wanted = bool(on)
        self._update_handle_visibility()

    def _update_handle_visibility(self):
        if getattr(self, 'handle', None) is None:
            return                       # 构造期被调到，把手还没生出来
        # ★ 顶上那条留白跟着「显示手柄」这个开关走 ★
        #   留白是给手柄让位的（它是独立小窗口，永远压在谱面窗顶上）。
        #   手柄不显示了还留着那 34 px，浮窗顶上就凭空多出一块空白。
        #   只看 `_handle_wanted`，不看 ghost / isVisible —— 那两个是
        #   "暂时不露面"，回来了还得有地方站。
        want = bool(getattr(self, '_handle_wanted', True))
        lay = self.layout()
        if lay is not None:
            m = lay.contentsMargins()
            top = HANDLE_H if want else 0
            if m.top() != top:
                lay.setContentsMargins(m.left(), top, m.right(), m.bottom())
                self.update()
        show = (want and self.isVisible() and not getattr(self, '_ghost', False))
        try:
            if show:
                self._sync_handle()
                self.handle.show()
                _raise_noactivate(self.handle)   # 压在谱面窗之上
            else:
                self.handle.hide()
        except Exception:
            pass

    def _sync_handle(self):
        """把把手摆到谱面窗顶上（同一个 x/y，宽度跟着走）。"""
        h = getattr(self, 'handle', None)
        if h is None:
            return
        h.setGeometry(self.x(), self.y(), max(120, self.width()), HANDLE_H)

    def _on_handle_drag_start(self):
        self._sync_handle()
        _raise_noactivate(self.handle)

    def _on_handle_drag(self, x: int, y: int):
        self.move(x, y)          # moveEvent 会把把手一起带走

    def _on_handle_drag_end(self):
        self._sync_handle()

    # ---- 视图 ----

    @property
    def view(self):
        return self.grid_view

    def set_timeline(self, tl: Timeline | None):
        self.grid_view.set_timeline(tl)

    def set_progress(self, sec: float, total: float):
        """把播放进度告诉顶上那条控制条。"""
        self.handle.set_progress(sec, total)

    def set_playing(self, on: bool):
        """把播放状态告诉控制条（按钮在 ▶ / ⏸ 之间切）。"""
        self.handle.set_playing(on)

    def set_song_name(self, text: str):
        """曲名行 —— 用户：「下面要显示哪首」。

        控制台载入谱面、或者浮窗改成跟制谱器手里那份时，都由那边改这里。
        （浮窗自己不知道曲库在哪儿，跟它不认识播放器是同一个道理。）
        """
        self.lbl_song.setText(str(text or ''))

    def set_sound(self, on: bool):
        """把「播放声音」的开关状态告诉控制条。"""
        self.handle.set_sound(on)

    # ---- 绘制 ----

    def paintEvent(self, _ev):
        """曲名那一行自己的底板。

        ★ 为什么在这儿画，而不是给 QLabel 设个背景色 ★

          「底板浓度」那个滑块管的是**整块浮窗**的浓淡 —— 拉到 0 就只剩字。
          曲名行要是写死一个半透明黑，拉到底的时候网格已经全透了、
          它自己还黑着一条，看着就像没跟上设置。

          所以这里取网格**同一份颜色、同一个比例**（`bg_scale` 在
          `GridView` 上），两边永远一致，而且不用把颜色抄第二遍。
        """
        g = self.grid_view
        scale = max(0.0, min(1.0, float(getattr(g, 'bg_scale', 1.0))))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        edge = QColor(T.BG_EDGE)
        edge.setAlpha(int(round(edge.alpha() * scale)))
        bg = QColor(T.BG)
        bg.setAlpha(int(round(bg.alpha() * scale)))
        p.setPen(QPen(edge, 2))
        p.setBrush(QBrush(bg))
        # ★ 位置读控件自己的几何，别写死 ★
        #   关掉「显示手柄」时上面那条留白会收掉（见 `_update_handle_visibility`），
        #   硬编码的 `HANDLE_H` 那时候就跟曲名行对不上了 —— 底板会画偏一格。
        r = self.lbl_song.geometry()
        p.drawRoundedRect(
            QRectF(r.x() + 1, r.y() + 1, r.width() - 2, r.height() - 2), 10, 10)

    def set_time(self, sec: float):
        self.view.set_time(sec)
        # 实时跟弹时当前格只闪 0.18 秒，得有人把它擦掉
        if (self.grid_view.current_blinking()
                and not self._flash_tick.isActive()):
            self._flash_tick.start()

    # ---- 实时跟弹的高亮 ----

    def flash_note(self, pitch: str, seconds: float = 0.35,
                   min_gap: float = 0.45):
        """让某个键亮一下 —— 「你按了这个」的即时反馈。

        `min_gap` 是同一个键的防抖间隔。默认 0.45 秒是为了治
        「按一下却闪 2~3 下」（识别器在采样衰减期会反复上报同一个键）。
        但**跟手模式**下每次都是真按，得把它调小 ——
        否则同一个键连按两下时第二下不闪，用户会觉得"没反应"。
        """
        """游戏里敲了哪个键，就在浮窗上亮哪个。

        时长不宜长：弹得快的时候（90ms 一个音）如果亮 0.7 秒，
        屏幕上会同时挂着七八个高亮，看着像是"按了 A 却亮了 B"。

        ★ 顺带做诊断 ★
          每 5 秒往终端打一行：这一段时间里闪了多少次、间隔多密、都是哪些键。
          「按一下却闪 4~5 下」这种问题光靠猜没用，得看真实的上报节奏。
        """
        self.grid_view.set_flash(pitch, seconds, min_gap=min_gap)
        if not self._flash_tick.isActive():
            self._flash_tick.start()
        now = time.monotonic()
        self._flog.append((now, pitch))
        if now - self._flog_t0 >= 5.0:
            self._flog_t0 = now
            rec = [(t, p) for t, p in self._flog if now - t <= 5.0]
            self._flog = rec[-400:]
            if rec:
                gaps = [rec[i + 1][0] - rec[i][0]
                        for i in range(len(rec) - 1)]
                med = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
                print('[flash] 5 秒内 %d 次　间隔中位 %.0f ms　最短 %.0f ms'
                      '　键：%s'
                      % (len(rec), med * 1000,
                         (min(gaps) * 1000) if gaps else 0.0,
                         ' '.join(p for _t, p in rec[:24])), flush=True)

    def _on_flash_tick(self):
        alive = self.grid_view.has_flash()
        self.grid_view.update()
        if not alive:
            self._flash_tick.stop()

    def clear_flash(self):
        self.grid_view.clear_flash()

    def set_mark_current(self, on: bool):
        """要不要涂黄「当前该打的音」（实时跟弹时关掉，只留青色高亮）。"""
        self.grid_view.set_mark_current(on)

    # ---- 鼠标穿透 ----

    @property
    def click_through(self) -> bool:
        return self._click_through

    def set_click_through(self, on: bool):
        """切换鼠标穿透。关掉之后浮窗的格子就能点了（「可按」）。"""
        self._click_through = bool(on)
        self._apply_exstyle()
        # ★ 这里原来还有两件事，都拿掉了 ★
        #
        # ① `setAttribute(WA_TransparentForMouseEvents, on)`
        #    —— 见构造函数里那一段：Qt 在**窗口创建那一刻**就把这个属性
        #    固化成了内部的输入穿透状态，之后再改**回不来**。
        #    所以原来"关掉穿透"关了个寂寞 —— 格子照样收不到鼠标，
        #    「可按按不动」就是这个。
        #    现在穿透**只**靠 `WS_EX_TRANSPARENT`（`_apply_exstyle`），
        #    这条路才真的通。
        #
        # ② `setCursor(SizeAllCursor / ArrowCursor)`
        #    —— 用户：「可按的时候……鼠标样式变了」。
        #    光标是设在**整窗**上的，子控件会继承，于是「可按」一开，
        #    整个谱面窗（连每一个格子）都变成四向箭头 ——
        #    看着像是能拖窗口，其实那儿拖不动，纯属误导。
        #    真正能拖的是把手：它自己的 `SizeAllCursor` 在
        #    `DragHandle.__init__` 里设着，跟这里无关。
        #
        # 关掉穿透之后谱面窗会浮到把手之上，把把手重新提起来
        # （不然按住把手拖，拖到一半就抓不住了）—— 这件事还得留着。
        self._update_handle_visibility()

    def _apply_exstyle(self):
        """切换「鼠标穿透」—— 两个坑都在这里填掉了。

        坑 1：光 SetWindowLong 不够。改完扩展样式必须再来一发
              SetWindowPos(SWP_FRAMECHANGED)，系统才会重算命中测试，
              否则鼠标照样穿过去 —— 表现就是「勾了允许拖动还是拖不动」。
        坑 2：关掉穿透时**不能**顺手去掉 WS_EX_NOACTIVATE。
              保留它，拖窗口的时候游戏才不会失焦，
              UE4 也就不会解除鼠标锁定（就是那个「鼠标飘出窗口」的老毛病）。
              窗口收得到鼠标消息，只是不会被激活，拖动照样好用。
        另外带 SWP_NOZORDER —— 绝不动 Z 序，免得又踩到 UE4 的全屏重算。
        """
        try:
            hwnd = int(self.winId())
            ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd),
                                        GWL_EXSTYLE) & 0xFFFFFFFF
            keep = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            if self._click_through:
                ex |= (WS_EX_TRANSPARENT | keep)
            else:
                ex = (ex | keep) & ~(WS_EX_TRANSPARENT & 0xFFFFFFFF)
            _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)
            _user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                 | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        except Exception:
            pass

    def ex_style(self) -> int:
        """当前扩展样式（诊断用）。"""
        return _ex_style(self)

    # ---- 临时隐身 ----

    def set_ghost(self, on: bool):
        """临时隐身：窗口还「在」（全局热键照收得到、鼠标照样抓得住），
        但**什么都不画** —— 因为窗口是 WA_TranslucentBackground，
        不画就等于完全透明。

        ★ 换成"不画内容"而不是 setWindowOpacity(0) ★
          后者会动 WS_EX_LAYERED，跟鼠标穿透的样式管理打架，
          实测勾上「允许拖动」后浮窗会变成一片白。
        """
        on = bool(on)
        if on == self._ghost:
            return
        self._ghost = on
        self.grid_view.set_hidden(on)
        # 隐身的时候把手也一起收起来 —— 不然"消失的浮窗"边上还挂着一根
        # 蓝条，看着像出错了。
        self._update_handle_visibility()

    @property
    def ghost(self) -> bool:
        return self._ghost

    def set_user_opacity(self, v: float):
        """用户设定的不透明度（隐身期间不生效，露脸时再套用）。"""
        self._user_opacity = max(0.05, min(1.0, float(v)))
        if not self._ghost:
            self.setWindowOpacity(self._user_opacity)

    # ---- 窗口事件 ----

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_exstyle()
        self._update_handle_visibility()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._update_handle_visibility()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_handle()          # 把手跟着谱面窗走

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_handle()
        if self.lbl_count.isVisible():
            self._relayout_count()

    def closeEvent(self, event):
        # ★ 把手是**独立的顶层窗口**，不关掉它进程退不干净 ★
        #   （Qt 的 lastWindowClosed 只看窗口，Tool 窗口也算一个）
        try:
            self.handle.close()
        except Exception:
            pass
        super().closeEvent(event)

    # ---- 拖动（只在关掉鼠标穿透时可用）----

    def mousePressEvent(self, event):
        if (not self._click_through
                and event.button() == Qt.MouseButton.LeftButton):
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (not self._click_through and self._drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # ---- 倒计时 ----

    def start_countdown(self, seconds: int, on_done):
        """在谱面窗上倒数若干秒，数完调用 on_done（用来争取就位时间）。"""
        self.cancel_countdown()
        if seconds <= 0:
            on_done()
            return
        self._count_left = int(seconds)
        self._count_done = on_done
        self._relayout_count()
        self.lbl_count.setText(str(self._count_left))
        self.lbl_count.show()
        self.lbl_count.raise_()
        self._count_timer.start()

    def cancel_countdown(self):
        self._count_timer.stop()
        self.lbl_count.hide()
        self._count_done = None
        self._count_left = 0

    @property
    def counting(self) -> bool:
        return self._count_left > 0

    def _on_count_tick(self):
        self._count_left -= 1
        if self._count_left <= 0:
            self._count_timer.stop()
            self.lbl_count.hide()
            cb = self._count_done
            self._count_done = None
            if cb:
                cb()
            return
        self.lbl_count.setText(str(self._count_left))

    def _relayout_count(self):
        w = max(120, min(self.width() - 40, 280))
        h = max(90, min(self.height() - 40, 210))
        self.lbl_count.setGeometry((self.width() - w) // 2,
                                   (self.height() - h) // 2, w, h)

    # ---- 小提示条 ----

    def show_toast(self, text: str, seconds: float = 2.5):
        """在谱面窗上闪一条提示 —— 用热键操作时给你反馈，又不会抢焦点。"""
        self.lbl_toast.setText(text)
        w = max(160, min(self.width() - 30, 400))
        h = max(46, min(self.height() // 4, 120))
        self.lbl_toast.setGeometry((self.width() - w) // 2,
                                   self.height() - h - 16, w, h)
        self.lbl_toast.show()
        self.lbl_toast.raise_()
        self._toast_timer.start(int(max(0.5, seconds) * 1000))

    def hide_toast(self):
        self._toast_timer.stop()
        self.lbl_toast.hide()

    # ---- 全局热键消息 ----

    def nativeEvent(self, event_type, message):
        """接 WM_HOTKEY。

        ★ 两个坑，都踩过 ★
        1. `self.hotkeys` 必须用 getattr 取 —— 这个方法可能在 QWidget 构造期间
           就被 Qt 调到，那时属性还没赋值。
        2. **结尾必须显式 `return False, 0`，不能 `return super().nativeEvent(...)`**。
           PyQt6 里基类实现可能返回 None，而 Qt 侧期望解包成 (bool, int)，
           结果就是进程直接 access violation（崩得毫无提示）。
        """
        hk = getattr(self, 'hotkeys', None)
        if hk is not None:
            try:
                msg = _MSG.from_address(int(message))
                if hk.handle_native(msg.message, msg.wParam):
                    return True, 0
            except Exception:
                pass
        return False, 0
