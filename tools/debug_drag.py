# -*- coding: utf-8 -*-
"""验证浮窗到底能不能拖 —— 不开游戏也能测。

直接给 OverlayWindow 发鼠标消息（按下 → 移动 → 松开），看它动不动。
这样能把「Windows 层收不收得到鼠标」和「Qt 层拖不拖得动」分开定位：
    WindowFromPoint 命中  → 鼠标到得了窗口
    这个脚本能拖动        → Qt 也处理了
两个都过却还是拖不动，那问题就在别处（比如拖动模式没真正打开）。

用法：python tools/debug_drag.py
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

from PyQt6.QtCore import Qt                     # noqa: E402
from PyQt6.QtWidgets import QApplication          # noqa: E402

from ui.overlay import OverlayWindow              # noqa: E402

_u = ctypes.windll.user32
_u.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
_u.PostMessageW.restype = wt.BOOL
_u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u.GetWindowLongW.restype = ctypes.c_long
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20

WM_LBUTTONDOWN, WM_MOUSEMOVE, WM_LBUTTONUP = 0x0201, 0x0200, 0x0202
MK_LBUTTON = 0x0001


def lp(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def exstyle(hwnd: int) -> int:
    return _u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF


class Probe(OverlayWindow):
    """装个探针，看 Qt 到底收没收到鼠标事件。"""

    def mousePressEvent(self, event):
        print('    [探针] mousePressEvent  pos=%s btn=%s'
              % (event.position(), event.button()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        print('    [探针] mouseMoveEvent   pos=%s buttons=%s'
              % (event.position(), event.buttons()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        print('    [探针] mouseReleaseEvent pos=%s' % (event.position(),))
        super().mouseReleaseEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    ov = Probe()
    ov.setGeometry(300, 300, 470, 580)
    ov.set_user_opacity(1.0)
    ov.show()
    am = app.processEvents()
    time.sleep(0.25)
    app.processEvents()

    hwnd = int(ov.winId())
    x0, y0 = 200, 300

    print('窗口 hwnd = %d  初始位置 = (%d, %d)'
          % (hwnd, ov.x(), ov.y()))
    print('  穿透_ON   click_through=%s  ex=0x%08X  透明位=%s'
          % (ov.click_through, exstyle(hwnd),
             bool(exstyle(hwnd) & WS_EX_TRANSPARENT)))

    # 打开拖动模式（等价于点「允许拖动」）
    ov.set_click_through(False)
    app.processEvents()
    print('  穿透_OFF click_through=%s  ex=0x%08X  透明位=%s'
          % (ov.click_through, exstyle(hwnd),
             bool(exstyle(hwnd) & WS_EX_TRANSPARENT)))

    # 模拟：按下 → 拖 → 松开
    # 先用同步的 SendMessage（直接调窗口过程），排除消息队列的问题
    print('  WA_TransparentForMouseEvents =',
          ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
    print('  --- 用真实鼠标事件驱动（会短暂挪动鼠标，别动它）---')

    # ★ 必须用真实鼠标 ★
    #   之前用 PostMessage/SendMessage 发 WM_LBUTTONDOWN，Qt 一点反应都没有 ——
    #   因为 Qt 的 Windows 后端会去看真实的鼠标位置/按键状态，
    #   光塞消息是假的。这里用 SetCursorPos + mouse_event 走完整路径。
    _u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    _u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _u.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                               ctypes.c_void_p]
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004

    save = wt.POINT()
    _u.GetCursorPos(ctypes.byref(save))

    cx = ov.x() + ov.width() // 2
    cy = ov.y() + ov.height() // 2
    _u.SetCursorPos(cx, cy)
    time.sleep(0.15)
    app.processEvents()

    _u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.05)
    app.processEvents()
    for i in range(1, 13):
        _u.SetCursorPos(cx + i * 8, cy + i * 4)
        time.sleep(0.02)
        app.processEvents()
    _u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.05)
    app.processEvents()

    _u.SetCursorPos(save.x, save.y)      # 把鼠标放回去
    time.sleep(0.05)
    real_moved = (ov.x() != 300) or (ov.y() != 300)
    print('  → 真实鼠标事件：%s' % ('拖动了' if real_moved else '没反应'))

    # ★ 光靠模拟鼠标不可靠 ★
    #   上面 SetCursorPos + mouse_event 未必真落到这个窗口上（可能是别的窗口
    #   在最前面）。所以再直接喂 Qt 事件，单独验证**拖动逻辑本身**对不对。
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent

    ov.move(300, 300)
    app.processEvents()

    def mk(kind, x, y):
        return QMouseEvent(kind, QPointF(x, y),
                           QPointF(ov.x() + x, ov.y() + y),
                           Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)

    ov.mousePressEvent(mk(QEvent.Type.MouseButtonPress, 200, 300))
    for i in range(1, 11):
        ov.mouseMoveEvent(mk(QEvent.Type.MouseMove, 200 + i * 8, 300 + i * 4))
    ov.mouseReleaseEvent(mk(QEvent.Type.MouseButtonRelease, 280, 340))
    app.processEvents()
    print('  → 直接喂 Qt 事件：位置 = (%d, %d)' % (ov.x(), ov.y()))

    moved = (ov.x() != 300) or (ov.y() != 300)
    print('-' * 60)
    print('拖完位置 = (%d, %d)' % (ov.x(), ov.y()))
    print('结论：%s' % ('能拖 ✓' if moved
                       else '拖不动 ✗（看上面两行是哪个环节断的）'))
    ov.close()
    return 0 if moved else 1


if __name__ == '__main__':
    sys.exit(main())
