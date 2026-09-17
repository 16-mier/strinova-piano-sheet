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

from PyQt6.QtCore import QElapsedTimer, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.timeline import Timeline

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


class Player(QObject):
    """播放时钟 —— 用真实经过的时间推进，不受界面卡顿影响。"""

    tick = pyqtSignal(float)
    state_changed = pyqtSignal(bool)      # True=正在播

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.sec = 0.0
        self.playing = False
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
        self._clock.restart()
        self.playing = True
        self._timer.start()
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
        self.tick.emit(self.sec)
        if was:
            self.state_changed.emit(False)

    def seek(self, sec: float):
        total = self.timeline.total_sec if self.timeline else 0.0
        self.sec = max(0.0, min(sec, total))
        self._base = self.sec
        if self.playing:
            self._clock.restart()
        self.tick.emit(self.sec)

    def nudge(self, delta: float):
        """微调播放位置（逐格排查用）。"""
        self.seek(self.sec + delta)

    # ---- 内部 ----

    def _on_tick(self):
        if not self.timeline:
            return
        self.sec = self._base + self._clock.elapsed() / 1000.0
        if self.sec >= self.timeline.total_sec:
            self.sec = self.timeline.total_sec
            self.tick.emit(self.sec)
            self.pause()
            return
        self.tick.emit(self.sec)


class OverlayWindow(QWidget):
    """置顶的谱面窗。"""

    def __init__(self, parent=None):
        super().__init__(None)
        self._click_through = True

        self.setWindowTitle('卡丘琴谱器')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                     # 不占任务栏
            | Qt.WindowType.WindowDoesNotAcceptFocus  # ★ 不抢焦点
        )

        self.grid_view = GridView(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.grid_view)

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
        self._flash_tick.setInterval(16)
        self._flash_tick.timeout.connect(self._on_flash_tick)
        self.resize(470, 580)

    # ---- 视图 ----

    @property
    def view(self):
        return self.grid_view

    def set_timeline(self, tl: Timeline | None):
        self.grid_view.set_timeline(tl)

    def set_time(self, sec: float):
        self.view.set_time(sec)

    # ---- 实时跟弹的高亮 ----

    def flash_note(self, pitch: str, seconds: float = 0.35):
        """游戏里敲了哪个键，就在浮窗上亮哪个。

        时长不宜长：弹得快的时候（90ms 一个音）如果亮 0.7 秒，
        屏幕上会同时挂着七八个高亮，看着像是"按了 A 却亮了 B"。
        """
        self.grid_view.set_flash(pitch, seconds)
        if not self._flash_tick.isActive():
            self._flash_tick.start()

    def _on_flash_tick(self):
        alive = self.grid_view.has_flash()
        self.grid_view.update()
        if not alive:
            self._flash_tick.stop()

    def clear_flash(self):
        self.grid_view.clear_flash()

    # ---- 鼠标穿透 ----

    @property
    def click_through(self) -> bool:
        return self._click_through

    def set_click_through(self, on: bool):
        """切换鼠标穿透。关掉之后就能拖动窗口来摆位置。"""
        self._click_through = bool(on)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        self._apply_exstyle()
        # 能抓的时候鼠标变"移动"样式，让人知道这里抓得住
        self.setCursor(Qt.CursorShape.SizeAllCursor if not on
                       else Qt.CursorShape.ArrowCursor)

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
        try:
            return _user32.GetWindowLongW(
                ctypes.c_void_p(int(self.winId())), GWL_EXSTYLE) & 0xFFFFFFFF
        except Exception:
            return 0

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.lbl_count.isVisible():
            self._relayout_count()

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
