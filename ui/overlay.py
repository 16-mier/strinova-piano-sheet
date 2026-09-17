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

from PyQt6.QtCore import QElapsedTimer, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.timeline import Timeline

from .views import FallView, GridView

# ---- Win32 常量（用于运行时切换鼠标穿透）----
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long


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
        self._timer.setInterval(16)                      # ~60fps
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
        self._mode = 'grid'

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
        self.fall_view = FallView(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.grid_view)
        lay.addWidget(self.fall_view)
        self.fall_view.hide()

        self._drag_offset = None
        self.resize(470, 580)

    # ---- 视图 ----

    @property
    def view(self):
        return self.grid_view if self._mode == 'grid' else self.fall_view

    def set_mode(self, mode: str):
        if mode not in ('grid', 'fall'):
            return
        self._mode = mode
        self.grid_view.setVisible(mode == 'grid')
        self.fall_view.setVisible(mode == 'fall')
        self.view.update()

    def toggle_mode(self) -> str:
        self.set_mode('fall' if self._mode == 'grid' else 'grid')
        return self._mode

    def set_timeline(self, tl: Timeline | None):
        self.grid_view.set_timeline(tl)
        self.fall_view.set_timeline(tl)

    def set_time(self, sec: float):
        self.view.set_time(sec)

    # ---- 鼠标穿透 ----

    @property
    def click_through(self) -> bool:
        return self._click_through

    def set_click_through(self, on: bool):
        """切换鼠标穿透。关掉之后就能拖动窗口来摆位置。"""
        self._click_through = bool(on)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        self._apply_exstyle()

    def _apply_exstyle(self):
        try:
            hwnd = int(self.winId())
            ex = _user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
            if self._click_through:
                ex |= (WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
                       | WS_EX_TOOLWINDOW)
            else:
                ex &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            _user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)
        except Exception:
            pass

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
