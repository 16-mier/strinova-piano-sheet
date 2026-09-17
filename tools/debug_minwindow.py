# -*- coding: utf-8 -*-
"""逐个加窗口标志，找出到底是谁让浮窗收不到鼠标事件。

浮窗（OverlayWindow）堆了一串标志和属性，其中某一个会让 Qt
根本不给它派发鼠标事件 —— 表现就是「勾了允许拖动却怎么都拖不动」。
这里从裸窗口开始一层层加，看是在哪一步断的。

用法：python tools/debug_minwindow.py
（会短暂挪动鼠标，别动它）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtCore import Qt                        # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

_u = ctypes.windll.user32
_u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_u.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                           ctypes.c_void_p]
LD, LU = 0x0002, 0x0004

F = Qt.WindowType
A = Qt.WidgetAttribute

app = QApplication(sys.argv)


class Probe(QWidget):
    def __init__(self):
        super().__init__(None)
        self.got = False

    def mousePressEvent(self, event):
        self.got = True
        super().mousePressEvent(event)


def trial(name: str, flags, attrs) -> bool:
    w = Probe()
    w.setWindowFlags(flags)
    for a in attrs:
        w.setAttribute(a, True)
    w.setGeometry(500, 500, 260, 160)
    w.setStyleSheet('background: rgba(230,60,60,150);')
    w.show()
    w.raise_()
    app.processEvents()
    time.sleep(0.25)
    app.processEvents()

    x = w.x() + w.width() // 2
    y = w.y() + w.height() // 2
    _u.SetCursorPos(x, y)
    time.sleep(0.12)
    app.processEvents()
    _u.mouse_event(LD, 0, 0, 0, None)
    time.sleep(0.06)
    app.processEvents()
    _u.mouse_event(LU, 0, 0, 0, None)
    time.sleep(0.06)
    app.processEvents()

    ok = w.got
    w.hide()
    w.close()
    app.processEvents()
    print('  %-46s %s' % (name, '收到 ✓' if ok else '**收不到 ✗**'))
    return ok


def main() -> int:
    print('从裸窗口开始，一层层加浮窗用到的标志：')
    print('-' * 66)
    trial('① 裸 QWidget', F.Window, [])
    trial('② +无边框', F.FramelessWindowHint, [])
    trial('③ +置顶', F.FramelessWindowHint | F.WindowStaysOnTopHint, [])
    trial('④ +Tool', F.FramelessWindowHint | F.WindowStaysOnTopHint
          | F.Tool, [])
    trial('⑤ +DoesNotAcceptFocus',
          F.FramelessWindowHint | F.WindowStaysOnTopHint | F.Tool
          | F.WindowDoesNotAcceptFocus, [])
    trial('⑥ +WA_TranslucentBackground',
          F.FramelessWindowHint | F.WindowStaysOnTopHint | F.Tool
          | F.WindowDoesNotAcceptFocus, [A.WA_TranslucentBackground])
    trial('⑦ +WA_ShowWithoutActivating（= 浮窗的真实配置）',
          F.FramelessWindowHint | F.WindowStaysOnTopHint | F.Tool
          | F.WindowDoesNotAcceptFocus,
          [A.WA_TranslucentBackground, A.WA_ShowWithoutActivating])
    trial('⑧ ⑦ + 顺手勾上 WA_TransparentForMouseEvents（对照组）',
          F.FramelessWindowHint | F.WindowStaysOnTopHint | F.Tool
          | F.WindowDoesNotAcceptFocus,
          [A.WA_TranslucentBackground, A.WA_ShowWithoutActivating,
           A.WA_TransparentForMouseEvents])
    return 0


if __name__ == '__main__':
    sys.exit(main())
