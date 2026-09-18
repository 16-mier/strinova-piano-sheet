# -*- coding: utf-8 -*-
"""探针：量游戏画面到底怎么动 —— 决定「浮窗跟随琴面」要用哪种追踪方案。

背景：用户说「即使画面移动也要贴合」，但**动多少、怎么动**没人量过。
- 如果只是呼吸级抖动（<2px）→ 校准一次就够，根本不用追踪；
- 如果是镜头平移（几十~几百 px）→ 必须做配准；
- 如果是透视变化（走近/走远）→ 必须做角点追踪 + 单应。

方法：相位相关（phase correlation）算相邻帧的**全局平移**。
这是最直接、无参数的位移估计：两帧频谱的归一化互功率谱反变换，
峰值位置 = 整数像素位移，峰值的尖锐度 = 该估计的可信度。

用法：
    python tools/probe_motion.py [秒数] [间隔ms]
    python tools/probe_motion.py 6 80
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import winfocus                                       # noqa: E402
from tools.grab_game import grab_desktop                        # noqa: E402


def to_gray(img, scale: float = 0.25):
    """下采样转灰度。降低分辨率既提速，又顺手压掉高频噪声。"""
    from PIL import Image
    w, h = img.size
    nw, nh = max(8, int(w * scale)), max(8, int(h * scale))
    g = img.convert('L').resize((nw, nh), Image.BILINEAR)
    return np.asarray(g, dtype=np.float64)


def shift_of(a: np.ndarray, b: np.ndarray):
    """相位相关：返回 b 相对 a 的位移 (dx, dy) 与峰值可信度。

    约定：`b` 是把 `a` 往右下挪 (dx, dy) 得到的 → 返回正的 dx, dy。
    """
    fa = np.fft.rfft2(a - a.mean())
    fb = np.fft.rfft2(b - b.mean())
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    cross /= mag
    cc = np.fft.irfft2(cross, s=a.shape)
    idx = int(np.argmax(cc))
    py, px = np.unravel_index(idx, cc.shape)
    peak = float(cc[py, px])
    # 次峰（排除主峰 3x3 邻域）= 噪声底，用来判断这次估计是否可信
    cc2 = cc.copy()
    h, w = cc.shape
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            cc2[(py + dy) % h, (px + dx) % w] = -1e9
    second = float(cc2.max())
    sy, sx = cc.shape
    if py > sy // 2:
        py -= sy
    if px > sx // 2:
        px -= sx
    return px, py, peak, second


def confidence(peak: float, second: float) -> float:
    """峰值/次峰比。真位移时会有一个尖锐的孤立峰。"""
    if second <= 0:
        return 999.0
    return peak / second


def band(a, box):
    """按 (x0f, y0f, x1f, y1f) 的**比例**裁一块区域出来。"""
    h, w = a.shape
    return a[int(h * box[1]):int(h * box[3]), int(w * box[0]):int(w * box[2])]


# 浮窗现在贴在游戏窗口左上角 → 中心区域天然避开它。
CENTRE = (0.25, 0.20, 0.75, 0.80)
# 左上角那一块 = 浮窗所在地，用来对照：如果这里算出「不动」而中心在动，
# 就说明浮窗确实在污染全局位移估计。
TOPLEFT = (0.02, 0.02, 0.22, 0.22)


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    gap_ms = float(sys.argv[2]) if len(sys.argv) > 2 else 80.0

    hwnd = winfocus.find_game_window()
    if not hwnd:
        print('没找到游戏窗口')
        return 1
    print('游戏 hwnd=%s  前台=%s' % (hwnd, winfocus.describe_foreground()))
    print('采样 %.1f 秒，间隔 %.0f ms（约 %d 帧）\n'
          % (secs, gap_ms, int(secs * 1000 / gap_ms)))

    prev_c = prev_t = None
    rows = []
    t0 = time.time()
    n = 0
    while time.time() - t0 < secs:
        tk = time.time()
        img, msg = grab_desktop(hwnd)
        if img is None:
            print('抓取失败：%s' % msg)
            return 1
        g = to_gray(img, 0.25)
        c = band(g, CENTRE)
        tl = band(g, TOPLEFT)
        n += 1
        if prev_c is not None:
            dx, dy, pk, sc = shift_of(prev_c, c)
            dxt, dyt, pkt, sct = shift_of(prev_t, tl)
            rows.append((tk - t0, dx, dy, pk, sc, dxt, dyt, pkt, sct))
        prev_c, prev_t = c, tl
        # 睡到下一拍（抓一帧约 30~60ms，故要扣掉）
        dt = gap_ms / 1000.0 - (time.time() - tk)
        if dt > 0:
            time.sleep(dt)

    if not rows:
        print('只抓到 %d 帧，没有可比对的两帧' % n)
        return 1

    print('共 %d 帧，%d 组帧间位移' % (n, len(rows)))
    print('图像尺寸（下采样后）%s  中心区 %s' % (prev_c.shape, band(prev_c, CENTRE).shape))
    print()
    print('  t(s)   中心位移     可信    左上角位移   可信')
    print('  ' + '-' * 52)
    for (t, dx, dy, pk, sc, dxt, dyt, pkt, sct) in rows:
        print('  %5.2f  (%+6.2f,%+6.2f) %7.2f   (%+6.2f,%+6.2f) %7.2f'
              % (t, dx, dy, confidence(pk, sc), dxt, dyt, confidence(pkt, sct)))

    d = np.array([[r[1], r[2]] for r in rows], dtype=np.float64)
    dtl = np.array([[r[5], r[6]] for r in rows], dtype=np.float64)
    conf = np.array([confidence(r[3], r[4]) for r in rows])
    conf_tl = np.array([confidence(r[7], r[8]) for r in rows])
    print()
    print('中心区：每帧位移 均值 (%.2f, %.2f) px  中位 (%.2f, %.2f) px  最大 %.2f px'
          % (d[:, 0].mean(), d[:, 1].mean(),
             np.median(d[:, 0]), np.median(d[:, 1]),
             np.hypot(d[:, 0], d[:, 1]).max()))
    print('        累计漂移 (%.2f, %.2f) px　可信度中位 %.2f'
          % (d[:, 0].sum(), d[:, 1].sum(), np.median(conf)))
    print('左上角：每帧位移 均值 (%.2f, %.2f) px  最大 %.2f px　可信度中位 %.2f'
          % (dtl[:, 0].mean(), dtl[:, 1].mean(),
             np.hypot(dtl[:, 0], dtl[:, 1]).max(), np.median(conf_tl)))
    print()
    # 下采样倍率 0.25 → 上面的像素数要 ×4 才是屏幕真实像素
    print('换算到原始分辨率（×4）：中心区最大 %.1f px，累计 %.1f px'
          % (np.hypot(d[:, 0], d[:, 1]).max() * 4,
             np.hypot(d[:, 0].sum(), d[:, 1].sum()) * 4))
    return 0


if __name__ == '__main__':
    sys.exit(main())
