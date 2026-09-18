# -*- coding: utf-8 -*-
"""抓游戏窗口的截图 —— 验证「直接看画面读按键」这条路。

思路：游戏里按下的键在画面上有视觉反馈（`shot2.png` 里 PAD11 就带着一圈白框）。
如果真是这样，那**根本不用识别音频** —— 直接看哪个键亮了就行：
准确率 100%，且完全不受 BGM / 枪声 / 角色语音影响。

用法：
    python tools/grab_game.py [输出.png]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import winfocus                                   # noqa: E402

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
        ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
        ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
        ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
        ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD),
    ]


def grab(hwnd: int):
    from PIL import Image
    rect = winfocus.window_rect(hwnd)
    if not rect:
        return None, '拿不到窗口矩形'
    _x, _y, w, h = rect
    if w < 16 or h < 16:
        return None, '窗口尺寸不对 %dx%d' % (w, h)
    hdc = _u.GetWindowDC(wt.HWND(hwnd))
    mdc = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    _g.SelectObject(mdc, bmp)
    ok = _u.PrintWindow(wt.HWND(hwnd), mdc, PW_RENDERFULLCONTENT)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h              # 负数 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    _g.DeleteObject(bmp)
    _g.DeleteDC(mdc)
    _u.ReleaseDC(wt.HWND(hwnd), hdc)
    img = Image.frombuffer('RGBA', (w, h), bytes(buf), 'raw', 'BGRA', 0, 1)
    img = img.convert('RGB')
    return img, ('PrintWindow 返回 %s　%d×%d' % (ok, w, h))


def grab_desktop(hwnd: int):
    """从**桌面 DC** 抓游戏窗口那块区域。

    UE4 的游戏窗口 `PrintWindow` 基本抓不到（返回 0、全黑），
    但游戏是无边框窗口、就摆在桌面上，直接从桌面截图那块矩形就行。
    前提：游戏窗口**没有被别的窗口挡住**。
    """
    from PIL import Image
    rect = winfocus.window_rect(hwnd)
    if not rect:
        return None, '拿不到窗口矩形'
    x, y, w, h = rect
    hdc = _u.GetDC(0)
    mdc = _g.CreateCompatibleDC(hdc)
    bmp = _g.CreateCompatibleBitmap(hdc, w, h)
    _g.SelectObject(mdc, bmp)
    ok = _g.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)   # SRCCOPY
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    _g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    _g.DeleteObject(bmp)
    _g.DeleteDC(mdc)
    _u.ReleaseDC(0, hdc)
    img = Image.frombuffer('RGBA', (w, h), bytes(buf), 'raw', 'BGRA', 0, 1)
    return img.convert('RGB'), ('桌面抓取 %s　%d×%d @(%d,%d)'
                                % ('OK' if ok else '失败', w, h, x, y))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    desktop = '--desktop' in sys.argv
    out = args[0] if args else os.path.join(
        os.path.expanduser('~'), 'Desktop', 'deepseek work', 'game_shot.png')
    hwnd = winfocus.find_game_window()
    print('游戏窗口 hwnd=%s　当前前台：%s'
          % (hwnd, winfocus.describe_foreground()))
    if not hwnd:
        print('没找到游戏窗口（游戏没开？）')
        return 1
    img, msg = (grab_desktop(hwnd) if desktop else grab(hwnd))
    print(msg)
    if img is None:
        return 1
    # 判一下是不是全黑/全白（DX 游戏常见 PrintWindow 抓不到）
    import numpy as np
    a = np.asarray(img)
    print('图像：%dx%d　均值 %.1f　标准差 %.1f　唯一色数 %d'
          % (a.shape[1], a.shape[0], a.mean(), a.std(),
             len(np.unique(a.reshape(-1, 3), axis=0))))
    img.save(out)
    print('已存：%s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
