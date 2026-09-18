# -*- coding: utf-8 -*-
"""探针：相位相关位移估计到底准不准 —— 给「跟随画面」定技术底座。

上一步 `probe_motion.py` 测出「静止时 0 位移」，但那个测量**是被污染的**：
采样区（中心 25%~75%）把浮窗自己也框进去了，而浮窗是不动的窗口，
它会把位移估计往 0 拽。所以「0」这个数字不能直接采信。

这里换成**可验证的离线测试**：拿一帧真实游戏画面，
自己按已知量平移 / 旋转，再让算法去估，看误差多大。
这样每一个数都有 ground truth 对照 —— 不需要用户配合，也不会自欺。

同时分区报告「可信度」，找出画面里哪块区域适合当追踪锚点。

用法：
    python tools/probe_shift_accuracy.py [图片路径]
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image                                            # noqa: E402


def to_gray(img, scale: float = 0.25):
    w, h = img.size
    nw, nh = max(8, int(w * scale)), max(8, int(h * scale))
    g = img.convert('L').resize((nw, nh), Image.BILINEAR)
    return np.asarray(g, dtype=np.float64)


def shift_of(a: np.ndarray, b: np.ndarray):
    """相位相关。返回 (dx, dy, peak, second)，b 相对 a 的位移。"""
    fa = np.fft.rfft2(a - a.mean())
    fb = np.fft.rfft2(b - b.mean())
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    cross /= mag
    cc = np.fft.irfft2(cross, s=a.shape)
    cc = np.fft.fftshift(cc)                       # 让 0 位移落在正中，取邻域才好写
    cy, cx = cc.shape[0] // 2, cc.shape[1] // 2
    dy0, dx0 = np.unravel_index(int(np.argmax(cc)), cc.shape)
    peak = float(cc[dy0, dx0])
    cc2 = cc.copy()
    h, w = cc.shape
    for j in range(-4, 5):
        for i in range(-4, 5):
            cc2[(dy0 + j) % h, (dx0 + i) % w] = -1e9
    second = float(cc2.max())
    return dx0 - cx, dy0 - cy, peak, second


def conf(pk: float, sc: float) -> float:
    return 999.0 if sc <= 0 else pk / sc


def subpixel(cc_peak_vals, axis_val):
    """三点抛物线插值，把整数峰细化成亚像素。"""
    a, b, c = cc_peak_vals
    denom = (a - 2 * b + c)
    if abs(denom) < 1e-12:
        return 0.0
    return 0.5 * (a - c) / denom


def band(a, box):
    h, w = a.shape
    return a[int(h * box[1]):int(h * box[3]), int(w * box[0]):int(w * box[2])]


# 各候选追踪区域（比例坐标）。名字后面的注释是它在实战里的意义。
REGIONS = {
    '全画面      ': (0.00, 0.00, 1.00, 1.00),
    '中带(含浮窗)': (0.25, 0.20, 0.75, 0.80),
    '下带(避浮窗)': (0.05, 0.45, 0.95, 0.80),
    '上带(避浮窗)': (0.30, 0.02, 0.95, 0.09),
    '左竖带      ': (0.02, 0.15, 0.16, 0.85),
    '右竖带      ': (0.84, 0.15, 0.98, 0.85),
}


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get('TEMP', '.'), 'gp_noov.png')
    img = Image.open(src)
    print('底图 %s  %dx%d' % (os.path.basename(src), img.width, img.height))

    SCALE = 0.25
    scale_px = 1.0 / SCALE                    # 下采样图上的 1px = 原图 4px
    full = to_gray(img, SCALE)
    print('下采样 %.2f → %dx%d（1px 下采样 = %.0fpx 原图）\n'
          % (SCALE, full.shape[1], full.shape[0], scale_px))

    # ---------- ① 已知平移，测估计误差 ----------
    print('① 已知平移 → 估计误差（下采样图上的像素；括号内是换算到原图）')
    print('  真值(dx,dy)   估计(dx,dy)   误差(px)   误差(原图px)   可信度')
    print('  ' + '-' * 66)
    shifts = [(1, 0), (0, 1), (3, -2), (-5, 4), (11, 7), (23, -17), (40, 33)]
    errs = []
    for (tdx, tdy) in shifts:
        # b = a 平移 (tdx, tdy)：用切片模拟，边缘会缺 → 两侧都裁掉同样多
        m = max(abs(tdx), abs(tdy)) + 2
        a = full[m:-m, m:-m]
        b = full[m + tdy:full.shape[0] - m + tdy,
                 m + tdx:full.shape[1] - m + tdx]
        gx, gy, pk, sc = shift_of(a, b)
        e = np.hypot(gx - tdx, gy - tdy)
        errs.append(e)
        print('  (%4d,%4d)     (%4d,%4d)    %5.2f       %6.2f        %6.2f'
              % (tdx, tdy, gx, gy, e, e * scale_px, conf(pk, sc)))
    print('  → 平移估计最大误差 %.2f px（下采样）= %.2f px（原图）\n'
          % (max(errs), max(errs) * scale_px))

    # ---------- ② 亚像素：半像素平移能不能估出来 ----------
    print('② 亚像素平移（真值不足 1px，看能不能分辨）')
    print('  真值(dx,dy)   估计(dx,dy)   误差(px)')
    print('  ' + '-' * 40)
    m = 4
    a0 = full[m:-m, m:-m]
    for (tdx, tdy) in [(0.5, 0.0), (0.25, 0.75), (-0.5, -0.5)]:
        # 用双线性平移造亚像素真值
        im = Image.fromarray(full.astype(np.uint8))
        shifted = im.transform(im.size, Image.AFFINE,
                               (1, 0, -tdx, 0, 1, -tdy), resample=Image.BILINEAR)
        b0 = np.asarray(shifted, dtype=np.float64)[m:-m, m:-m]
        gx, gy, pk, sc = shift_of(a0, b0)
        print('  (%4.2f,%4.2f)   (%4d,%4d)    %5.2f  ← 整数峰，未细分'
              % (tdx, tdy, gx, gy, np.hypot(gx - tdx, gy - tdy)))

    # ---------- ③ 旋转：纯平移假设什么时候会崩 ----------
    print('\n③ 旋转 → 纯平移模型的残差（残差大 = 必须用单应，不能只平移）')
    print('  旋转角度   估计(dx,dy)   可信度   峰值占比')
    print('  ' + '-' * 46)
    for ang in (0.0, 0.5, 1.0, 2.0, 5.0):
        im = Image.fromarray(full.astype(np.uint8))
        rot = im.rotate(ang, resample=Image.BILINEAR, center=(im.width / 2,
                                                              im.height / 2))
        b = np.asarray(rot, dtype=np.float64)
        m = 6
        a = full[m:-m, m:-m]
        bb = b[m:-m, m:-m]
        gx, gy, pk, sc = shift_of(a, bb)
        print('  %5.1f°      (%+5d,%+5d)   %6.2f   %7.3f'
              % (ang, gx, gy, conf(pk, sc), pk))

    # ---------- ④ 分区可信度：哪块适合当锚点 ----------
    print('\n④ 分区可信度（相邻两帧同区域，静止画面。可信度高=纹理好、适合当锚点）')
    print('  区域            尺寸(下采样)   可信度   峰值')
    print('  ' + '-' * 52)
    a_prev = full
    # 用同一张图加极小噪声模拟"两帧"：真画面静止时帧间只有噪声/动画
    rng = np.random.default_rng(7)
    a_next = full + rng.normal(0, 1.0, full.shape)
    for name, box in REGIONS.items():
        ra = band(a_prev, box)
        rb = band(a_next, box)
        if min(ra.shape) < 8:
            print('  %s 太小，跳过' % name)
            continue
        gx, gy, pk, sc = shift_of(ra, rb)
        print('  %s  %4dx%-4d      %6.2f   %6.4f'
              % (name, ra.shape[1], ra.shape[0], conf(pk, sc), pk))
    return 0


if __name__ == '__main__':
    sys.exit(main())
