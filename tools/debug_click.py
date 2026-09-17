# -*- coding: utf-8 -*-
"""诊断「允许拖动」到底有没有生效 —— 不开游戏也能测。

原理：`WindowFromPoint` 会**跳过** WS_EX_TRANSPARENT 的窗口。
所以拿浮窗正中心那一点去问系统「这一点上是谁」，就能知道
鼠标点下去会不会被浮窗接住：

    返回我们自己 -> 能点到（拖动可用）✓
    返回别的窗口 -> 还在穿透（拖不动）✗

用法：python tools/debug_click.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtWidgets import QApplication          # noqa: E402

app = QApplication(sys.argv)

from ui.overlay import (GWL_EXSTYLE, WS_EX_LAYERED,     # noqa: E402
                        WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                        WS_EX_TRANSPARENT, OverlayWindow)

_u = ctypes.windll.user32
_u.WindowFromPoint.argtypes = [wt.POINT]
_u.WindowFromPoint.restype = wt.HWND
_u.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
_u.GetAncestor.restype = wt.HWND
GA_ROOT = 2


def flag_str(ex: int) -> str:
    out = []
    for name, bit in (('TRANSPARENT', WS_EX_TRANSPARENT),
                      ('LAYERED', WS_EX_LAYERED),
                      ('NOACTIVATE', WS_EX_NOACTIVATE),
                      ('TOOLWINDOW', WS_EX_TOOLWINDOW)):
        if ex & bit:
            out.append(name)
    return '|'.join(out) or '(无)'


def probe(ov, tag: str) -> bool:
    """返回 True 表示「鼠标能被浮窗接住」。"""
    hwnd = int(ov.winId())
    dpr = float(ov.devicePixelRatioF() or 1.0) or 1.0
    ex = _u.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF

    # Qt 逻辑坐标 -> 物理像素
    cx = int((ov.x() + ov.width() / 2) * dpr)
    cy = int((ov.y() + ov.height() / 2) * dpr)
    pt = wt.POINT(cx, cy)
    hit = _u.WindowFromPoint(pt)
    root = int(_u.GetAncestor(hit, GA_ROOT)) if hit else 0
    ok = (root == hwnd)

    print('%-14s ex=0x%08X [%s]' % (tag, ex, flag_str(ex)))
    print('   dpr=%.2f  探测点(物理)=%s  WindowFromPoint=%d  root=%d  -> %s'
          % (dpr, (cx, cy), int(hit or 0), root,
             'HIT 浮窗能接住鼠标 ✓' if ok else 'PASS 穿透中 ✗'))
    return ok


def main() -> int:
    ov = OverlayWindow()
    ov.setGeometry(200, 200, 470, 580)
    ov.set_user_opacity(1.0)
    ov.show()
    app.processEvents()

    print('浮窗 hwnd = %d' % int(ov.winId()))
    print('=' * 68)
    ok_on = probe(ov, '穿透=ON')
    print('-' * 68)
    ov.set_click_through(False)
    app.processEvents()
    ok_off = probe(ov, '穿透=OFF')
    print('-' * 68)
    ov.set_click_through(True)
    app.processEvents()
    ok_back = probe(ov, '再开穿透')
    print('=' * 68)

    verdict = []
    verdict.append(('穿透开时应当穿过去', not ok_on))
    verdict.append(('关穿透后应当能接住鼠标', ok_off))
    verdict.append(('重新开穿透应当又穿过去', not ok_back))
    allok = True
    for name, good in verdict:
        print('  [%s] %s' % ('PASS' if good else 'FAIL', name))
        allok = allok and good
    print('=' * 68)
    print('结论：%s' % ('鼠标穿透开关工作正常' if allok else '有问题，见上面'))
    ov.close()
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
