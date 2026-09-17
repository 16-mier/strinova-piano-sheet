# -*- coding: utf-8 -*-
"""整屏 / 指定窗口截图（开发时看效果用）。

用法：
    python tools/shot.py out.png                  # 整个虚拟桌面
    python tools/shot.py out.png x y w h          # 指定矩形
    python tools/shot.py out.png --ctrl           # 「卡丘琴谱器」控制台窗口
    python tools/shot.py out.png --title 记事本    # 标题包含某段文字的第一个窗口
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from PyQt6.QtWidgets import QApplication          # noqa: E402

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32
_u.GetWindowTextLengthW.argtypes = [wt.HWND]
_u.GetWindowTextLengthW.restype = ctypes.c_int
_u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_u.GetWindowTextW.restype = ctypes.c_int
_u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_u.GetWindowRect.restype = wt.BOOL
_ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wt.WORD),
                ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [('bmiHeader', _BMIH), ('bmiColors', wt.DWORD * 3)]


_g.CreateCompatibleDC.argtypes = [wt.HDC]
_g.CreateCompatibleDC.restype = wt.HDC
_g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_g.CreateCompatibleBitmap.restype = wt.HBITMAP
_g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_g.SelectObject.restype = wt.HGDIOBJ
_g.DeleteObject.argtypes = [wt.HGDIOBJ]
_g.DeleteDC.argtypes = [wt.HDC]
_u.GetWindowDC.argtypes = [wt.HWND]
_u.GetWindowDC.restype = wt.HDC
_u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_u.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
_u.PrintWindow.restype = wt.BOOL
_g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                         ctypes.c_void_p, ctypes.POINTER(_BMI), ctypes.c_uint]
_g.GetDIBits.restype = ctypes.c_int


def windows_with(title_part: str):
    """[(hwnd, 标题, (x, y, w, h))]，按标题子串找。"""
    hits = []

    def _cb(hwnd, _lp):
        try:
            n = _u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 2)
            _u.GetWindowTextW(hwnd, buf, n + 2)
            if title_part in buf.value:
                r = wt.RECT()
                if _u.GetWindowRect(hwnd, ctypes.byref(r)):
                    w = r.right - r.left
                    h = r.bottom - r.top
                    if w > 40 and h > 40:
                        hits.append((int(hwnd), buf.value,
                                     (r.left, r.top, w, h)))
        except Exception:
            pass
        return True

    try:
        _u.EnumWindows(_ENUM(_cb), 0)
    except Exception:
        pass
    return hits


def grab_by_printwindow(hwnd: int, out: str):
    """PrintWindow 抓窗口画面 —— 被别的窗口挡住也照样抓得到。"""
    from PyQt6.QtGui import QImage
    r = wt.RECT()
    if not _u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        return 0, 0
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return 0, 0
    hdc = _u.GetWindowDC(wt.HWND(hwnd))
    mem = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    old = _g.SelectObject(mem, bmp)
    # 2 = PW_RENDERFULLCONTENT：DWM 合成的内容（Qt 窗口）也能抓到
    if not _u.PrintWindow(wt.HWND(hwnd), mem, 2):
        _u.PrintWindow(wt.HWND(hwnd), mem, 0)

    bi = _BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # 负 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    _g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    _g.SelectObject(mem, old)
    _g.DeleteObject(bmp)
    _g.DeleteDC(mem)
    _u.ReleaseDC(wt.HWND(hwnd), hdc)

    img = QImage(buf, w, h, QImage.Format.Format_ARGB32)
    img.save(out)                        # buf 得活到这儿
    return w, h


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    out = args[0]
    rect = None
    hwnd = 0

    if '--ctrl' in args or '--title' in args:
        want = '卡丘琴谱器'
        if '--title' in args:
            i = args.index('--title')
            if i + 1 < len(args):
                want = args[i + 1]
        hits = windows_with(want)
        if not hits:
            print('没找到标题含 %r 的窗口' % want)
            return 1
        # 多个同名窗口时取最「高」的那个（控制台比浮窗高）
        hwnd = max(hits, key=lambda t: t[2][3])[0]
        print('窗口：%s  %s' % (hits[0][1], hits[0][2]))
    elif len(args) >= 5:
        rect = tuple(int(v) for v in args[1:5])

    app = QApplication([])
    scr = app.primaryScreen()

    if hwnd and '--raise' in args:
        # PrintWindow 抓 Qt 的硬件渲染窗口常常是一片白，所以：
        # 临时置顶（不激活，游戏不会失焦）→ 屏幕抓 → 立刻取消置顶。
        _u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_uint]
        _u.SetWindowPos(wt.HWND(hwnd), wt.HWND(-1), 0, 0, 0, 0,  # HWND_TOPMOST
                        0x0002 | 0x0001 | 0x0010 | 0x0040)       # NOMOVE|NOSIZE
        time.sleep(0.45)                                          # |NOACT|SHOW
        r = wt.RECT()
        _u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
        pm = scr.grabWindow(0, r.left, r.top, r.right - r.left,
                            r.bottom - r.top)
        _u.SetWindowPos(wt.HWND(hwnd), wt.HWND(-2), 0, 0, 0, 0,  # NOTOPMOST
                        0x0002 | 0x0001 | 0x0010)
        if pm.isNull():
            print('截图失败')
            return 1
        pm.save(out)
        print('已保存 %s  (%dx%d，临时置顶抓的)' % (out, pm.width(), pm.height()))
        return 0

    if hwnd:
        w, h = grab_by_printwindow(hwnd, out)
        if not w:
            print('抓窗口画面失败')
            return 1
        print('已保存 %s  (%dx%d，PrintWindow)' % (out, w, h))
        return 0

    if scr is None:
        print('没有屏幕？')
        return 1
    if rect:
        pm = scr.grabWindow(0, rect[0], rect[1], rect[2], rect[3])
    else:
        pm = scr.grabWindow(0)
    if pm.isNull():
        print('截图失败')
        return 1
    pm.save(out)
    print('已保存 %s  (%dx%d)' % (out, pm.width(), pm.height()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
