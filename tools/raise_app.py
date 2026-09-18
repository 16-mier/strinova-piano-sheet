# -*- coding: utf-8 -*-
"""把「卡丘琴谱器」窗口提到最前（开发时用）。

用法：python tools/raise_app.py
先试 SetForegroundWindow（会一起给焦点）；被前台锁定挡住的话，
退回「临时置顶」——至少让你能看见它。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

sys.stdout.reconfigure(encoding='utf-8')

from shot import windows_with                        # noqa: E402

_u = ctypes.windll.user32
_u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u.SetForegroundWindow.argtypes = [wt.HWND]
_u.SetForegroundWindow.restype = wt.BOOL
_u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u.BringWindowToTop.argtypes = [wt.HWND]

SW_RESTORE = 9
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x2, 0x1, 0x10, 0x40


def main() -> int:
    kw = sys.argv[1] if len(sys.argv) > 1 else '卡丘琴谱器'
    hits = windows_with(kw)
    if not hits:
        print('没找到「%s」窗口 —— 是不是没启动？' % kw)
        return 1
    # 多个命中时取最高的那个（控制台比谱面浮窗高）
    ctrl = max(hits, key=lambda t: t[2][3])
    hwnd = ctrl[0]
    print('窗口：%s  %s' % (ctrl[1], ctrl[2]))

    _u.ShowWindow(wt.HWND(hwnd), SW_RESTORE)
    ok = _u.SetForegroundWindow(wt.HWND(hwnd))
    if ok:
        print('已提到最前并给上焦点 ✓')
        return 0
    # 前台锁定（Windows 不让后台进程抢焦点）—— 退回置顶
    _u.BringWindowToTop(wt.HWND(hwnd))
    _u.SetWindowPos(wt.HWND(hwnd), wt.HWND(-1), 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    print('抢不到焦点，已临时置顶（你能看见它，点一下就能操作）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
