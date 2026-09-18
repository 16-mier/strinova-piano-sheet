# -*- coding: utf-8 -*-
"""探针：抓屏幕到底能多快 —— 现在 130ms/帧是整条链路的上限。

★ 为什么先量这个 ★
  实测（`tools/grab_game.py::grab_desktop`，抓整个游戏窗口 3429×2128）：
      **130.1 ms/次** —— 也就是最高 7.7 fps。
  而"跟随画面"至少要 15~30fps，否则两帧之间画面早就跑出搜索范围了
  （0.23 秒的间隔下，用户转一下视角，各块位移估计全是噪声，可信度掉到 1~3）。

  在讨论"用哪个算法"之前，得先知道**到底能拿到多少帧**。
  喂不进帧，再好的算法也没用。

★ 量三个变量 ★
  ① **只抓需要的区域** —— 面积比 7:1 的窗口 vs 小块，这是最直接的
  ② **复用 DC / 位图** —— 现在每次调用都 CreateCompatibleDC + CreateCompatibleBitmap
     + DeleteObject，这些是实实在在的开销
  ③ **CreateDIBSection 直取指针** —— 绕开 GetDIBits（它要再拷一遍）

用法：
    python tools/probe_grab_speed.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import winfocus                                       # noqa: E402

_u = ctypes.windll.user32
_g = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
        ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
        ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
        ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
        ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


class Grabber:
    """复用 DC / 位图 / 缓冲的抓屏器 —— 只有尺寸变了才重建。"""

    def __init__(self):
        self._hdc = None
        self._mdc = None
        self._bmp = None
        self._old = None
        self._buf = None
        self._bi = None
        self._size = (0, 0)
        self._bits = None            # CreateDIBSection 方案的指针

    def _ensure(self, w: int, h: int):
        if self._size == (w, h):
            return
        self.free()
        self._hdc = _u.GetDC(0)
        self._mdc = _g.CreateCompatibleDC(self._hdc)
        # ★ CreateDIBSection：位图的像素**直接就是我们能读的内存** ★
        #   于是抓完不用再 GetDIBits 拷一遍（那一步在 3429×2128 上是几十毫秒）
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0      # BI_RGB
        ppv = ctypes.c_void_p()
        self._bmp = _g.CreateDIBSection(self._mdc, ctypes.byref(bi),
                                        0, ctypes.byref(ppv), None, 0)
        self._bits = ppv
        self._old = _g.SelectObject(self._mdc, self._bmp)
        self._bi = bi
        self._size = (w, h)
        # 直接在这块内存上做 numpy 视图（零拷贝）
        self._buf = np.ctypeslib.as_array(
            (ctypes.c_ubyte * (w * h * 4)).from_address(ppv.value))

    def grab_np(self, x: int, y: int, w: int, h: int):
        """抓一块区域，返回 (h, w, 3) 的 BGR numpy 数组（**视图，不拷贝**）。"""
        self._ensure(w, h)
        _g.BitBlt(self._mdc, 0, 0, w, h, self._hdc, x, y, SRCCOPY)
        a = self._buf.reshape(h, w, 4)
        return a[:, :, :3]

    def free(self):
        if self._mdc:
            if self._old:
                _g.SelectObject(self._mdc, self._old)
            if self._bmp:
                _g.DeleteObject(self._bmp)
            _g.DeleteDC(self._mdc)
        if self._hdc:
            _u.ReleaseDC(0, self._hdc)
        self._hdc = self._mdc = self._bmp = self._old = None
        self._size = (0, 0)
        self._buf = None
        self._bits = None


def bench(fn, n: int = 12) -> float:
    for _ in range(3):
        fn()                                  # 预热
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1000.0


def main() -> int:
    hwnd = winfocus.find_game_window()
    if not hwnd:
        print('没找到游戏窗口')
        return 1
    rect = winfocus.window_rect(hwnd)
    print('游戏窗口 rect=%s  hwnd=%s\n' % (rect, hwnd))
    wx, wy, ww, wh = rect

    # ---------- ① 老路：每次重建 + GetDIBits + PIL ----------
    from tools.grab_game import grab_desktop
    t_old_full = bench(lambda: grab_desktop(hwnd), 6)
    print('① 老路 grab_desktop（每次重建 DC/位图 + GetDIBits + 转 PIL）')
    print('   全窗口 %dx%d : %7.1f ms   → %5.1f fps\n'
          % (ww, wh, t_old_full, 1000.0 / t_old_full))

    # ---------- ② 新路：复用 + DIBSection，只抓区域 ----------
    g = Grabber()
    print('② 复用 DC/位图 + CreateDIBSection 直取指针（返回 numpy 视图）')
    print('   区域大小            耗时(ms)    可达fps   相对全窗口')
    print('   ' + '-' * 54)
    cases = [(ww, wh), (ww // 2, wh // 2), (1200, 1200), (900, 900),
             (600, 600), (400, 400), (256, 256)]
    base = None
    for (cw, ch) in cases:
        # 尽量落在窗口内
        x = wx + max(0, (ww - cw) // 2)
        y = wy + max(0, (wh - ch) // 2)
        ms = bench(lambda cw=cw, ch=ch, x=x, y=y: g.grab_np(x, y, cw, ch))
        if base is None:
            base = ms
        print('   %5dx%-5d  %10.2f  %8.1f   %6.2fx'
              % (cw, ch, ms, 1000.0 / ms, base / ms))
    g.free()

    # ---------- ③ 抓完直接算位移要多少时间 ----------
    print('\n③ 抓完之后"算一次位移"本身的开销（下采样 + FFT）')
    gg = Grabber()
    arr = gg.grab_np(wx + 600, wy + 400, 900, 900).copy()
    gg.free()
    gray = np.asarray(arr, dtype=np.float64).mean(axis=2)

    def downsample_fft(a):
        b = a[::2, ::2]                       # 简单 2x2 平均的效果接近
        fa = np.fft.rfft2(b - b.mean())
        fb = np.fft.rfft2(b - b.mean())
        c = fa * np.conj(fb)
        c /= np.maximum(np.abs(c), 1e-12)
        return np.fft.irfft2(c, s=b.shape)

    ms = bench(lambda: downsample_fft(gray), 12)
    print('   900x900 灰度 → 2x 下采样 + 两次 rfft2 + irfft2 : %.2f ms' % ms)
    print('   （也就是说抓帧之外，算法本身只要几毫秒，瓶颈**完全在抓屏**）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
