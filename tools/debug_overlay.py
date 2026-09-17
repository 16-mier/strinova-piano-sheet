# -*- coding: utf-8 -*-
"""定位 OverlayWindow 崩溃：把它的 __init__ 原样复制成测试类。"""

import faulthandler
import sys

faulthandler.enable()
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'C:\Users\mier\Desktop\deepseek work\strinova-piano-sheet')

from PyQt6.QtCore import Qt, QTimer                 # noqa: E402
from PyQt6.QtWidgets import (QApplication, QLabel,  # noqa: E402
                             QVBoxLayout, QWidget)

app = QApplication([])
print('app ok', flush=True)

from ui.views import FallView, GridView             # noqa: E402

# ★ 关键：导入 ui.overlay（会执行它模块级的 ctypes 设置）
import ui.overlay as _ov                             # noqa: E402,E402
print('ui.overlay imported', flush=True)
print('  OverlayWindow =', _ov.OverlayWindow, flush=True)


class CopyWin(QWidget):
    """OverlayWindow.__init__ 的逐行复制。"""

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
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )

        self.grid_view = GridView(self)
        self.fall_view = FallView(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.grid_view)
        lay.addWidget(self.fall_view)
        self.fall_view.hide()

        self.lbl_count = QLabel('', self)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setStyleSheet(
            'QLabel{color:#ffd230;font-size:72px;font-weight:bold;'
            'background:rgba(8,10,16,190);border-radius:20px;'
            'border:3px solid rgba(255,210,48,160);}')
        self.lbl_count.hide()

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

        self.hotkeys = None
        self._count_left = 0
        self._count_done = None
        self._count_timer = QTimer(self)
        self._count_timer.setInterval(1000)
        self._drag_offset = None
        self.resize(470, 580)


print('creating CopyWin…', flush=True)
w = CopyWin()
print('ctor ok', flush=True)
print('winId =', int(w.winId()), flush=True)
print('COPY OK', flush=True)

print('\n--- 现在创建真正的 OverlayWindow ---', flush=True)
try:
    real = _ov.OverlayWindow()
    print('real ctor ok', flush=True)
    print('real winId =', int(real.winId()), flush=True)
    print('REAL OK', flush=True)
except Exception as e:
    print('real failed: %r' % e, flush=True)
