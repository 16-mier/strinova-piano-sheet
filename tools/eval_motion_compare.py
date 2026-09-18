# -*- coding: utf-8 -*-
"""在**真实游戏画面**上对比两条"画面运动估计"路线，看哪条够用。

★ 为什么要这个对比 ★
  自己那条（分块相位相关 + 拟合仿射）在合成纹理上测试全绿，
  但合成纹理**没有水面、天空、植被、角色动画** —— 那些低纹理/动态区域
  会让大量块不可信。所以必须换真实画面重新量。

  真机上拿不到 ground truth（我不知道用户当时到底转了几度），
  所以：拿**一帧真实的游戏画面**，人工施加**已知的**旋转+平移，用它当"下一帧"。
  纹理是真的，真值也是真的。

★ 两条路线 ★
  `phase` —— `core/motion.py`：分块相位相关 + 最小二乘拟合仿射。零依赖。
  `flow`  —— OpenCV：`goodFeaturesToTrack` + `calcOpticalFlowPyrLK`
             + `estimateAffinePartial2D(RANSAC)`。需要 opencv-python。

★ 看什么 ★
  · **浮窗四角的跟随误差（px）** —— 用户真正在意的量
  · 可信块数 / 有效特征点数（够不够支撑拟合）
  · 速度

★ 方向约定（子代理实测标定过，这里跟着走）★
  `estimateAffinePartial2D(f1点, f2点)` 返回的 `M` 满足 `x_f2 = M·[x_f1, 1]`，
  也就是**内容映射** —— 浮窗四角直接左乘 `M`，**不要取逆**。
  要和本项目的 `d(p) = A·p + t` 形式互转，就是 `A = M[:, :2] - I`、`t = M[:, 2]`。

用法：
    python tools/eval_motion_compare.py [底图]
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image                                            # noqa: E402
from scipy import ndimage                                        # noqa: E402

from core import motion                                          # noqa: E402

try:
    import cv2
    cv2.setNumThreads(8)          # ★ 白捡 3.7 倍：128 线程会把 LK 拖慢到 11.19ms ★
    HAVE_CV = True
except ImportError:
    HAVE_CV = False

# 浮窗在画面里的位置（用户那次标定后的实际值，换算到图片坐标）
OV = (3209 - 1701, 492, 3648 - 1701, 1112)
PAD = 20


def load_pair(path, scale=0.5):
    """读成 float64 灰度（相位相关用）和 uint8 灰度（OpenCV 用）。"""
    im = Image.open(path).convert('L')
    w, h = im.size
    if scale != 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    u8 = np.asarray(im, dtype=np.uint8)
    return np.asarray(u8, dtype=np.float64), u8


def simulate(img, deg: float, dx: float, dy: float):
    """造"下一帧"：绕画面中心旋转，再平移（内容映射，方向与相位相关一致）。"""
    rot = ndimage.rotate(img, deg, reshape=False, order=1, mode='nearest')
    return ndimage.shift(rot, (dy, dx), order=1, mode='nearest')


def truth_matrix(deg, dx, dy, cx, cy):
    """真值（`d(p) = A·p + t` 形式），A = R - I。"""
    th = np.radians(deg)
    R = np.array([[np.cos(th), np.sin(th)], [-np.sin(th), np.cos(th)]])
    A = R - np.eye(2)
    t = np.array([dx, dy]) + np.array([cx, cy]) - R @ np.array([cx, cy])
    return A, t


def mask_excluding(shape, ov):
    """全 255，浮窗那块置 0 —— 光流找特征点时用它排除掉浮窗。"""
    m = np.full(shape, 255, np.uint8)
    if ov:
        x0, y0, x1, y1 = [int(v) for v in ov]
        m[max(0, y0):y1, max(0, x0):x1] = 0
    return m


# ------------------------------------------------------------ 路线 1：phase

def run_phase(a_f, b_f, ov, cols=4, rows=3, ds=4):
    h, w = a_f.shape
    hole = (ov[0] - PAD, ov[1] - PAD, ov[2] + PAD, ov[3] + PAD) if ov else None
    blocks = motion.grid_blocks(
        w, h, cols=cols, rows=rows,
        skip=(lambda r: motion.rects_overlap(r, hole)) if hole else None)
    prev, cur = {}, {}
    for (_cx, _cy, x0, y0, bw, bh) in blocks:
        key = (x0 // ds, y0 // ds)
        prev[key] = a_f[::ds, ::ds][y0 // ds:(y0 + bh) // ds, x0 // ds:(x0 + bw) // ds]
        cur[key] = b_f[::ds, ::ds][y0 // ds:(y0 + bh) // ds, x0 // ds:(x0 + bw) // ds]
    t0 = time.perf_counter()
    field = motion.sample_shift_field(prev, cur, ds=ds)
    r = motion.fit_affine(field)
    ms = (time.perf_counter() - t0) * 1000.0
    return r, len(field), len(blocks), ms


# ------------------------------------------------------------- 路线 2：flow

def run_flow(a_u8, b_u8, ov, max_pts=500):
    if not HAVE_CV:
        return None, 0, 0, 0.0
    t0 = time.perf_counter()
    m = mask_excluding(a_u8.shape, ov)
    pts = cv2.goodFeaturesToTrack(a_u8, max_pts, 0.01, 10, mask=m, blockSize=7)
    if pts is None or len(pts) < 12:
        return None, 0 if pts is None else len(pts), 0, (time.perf_counter() - t0) * 1000
    p2, st, _err = cv2.calcOpticalFlowPyrLK(
        a_u8, b_u8, pts, None, winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    good = st.ravel() == 1
    n = int(good.sum())
    if n < 12:
        return None, n, len(pts), (time.perf_counter() - t0) * 1000
    M, inl = cv2.estimateAffinePartial2D(
        pts[good], p2[good], method=cv2.RANSAC,
        ransacReprojThreshold=3.0, maxIters=2000, confidence=0.995,
        refineIters=10)
    ms = (time.perf_counter() - t0) * 1000.0
    if M is None:
        return None, n, len(pts), ms
    # M 是内容映射：x' = M·[x,1]。转成本项目的 d(p) = A·p + t 形式
    A = M[:, :2] - np.eye(2)
    t = M[:, 2]
    return (A, t), n, len(pts), ms


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    src = args[0] if args else os.path.join(ROOT, '_fit_source_frame.png')
    if not os.path.exists(src):
        print('找不到底图 %s' % src)
        return 1

    SCALE = 0.5
    a_f, a_u8 = load_pair(src, SCALE)
    h, w = a_f.shape
    ov = tuple(int(v * SCALE) for v in OV)
    print('底图 %s → %dx%d（缩放 %.2f）' % (os.path.basename(src), w, h, SCALE))
    print('浮窗区域 %s  占画面 %.1f%%' % (ov, (ov[2] - ov[0]) * (ov[3] - ov[1])
                                          / float(w * h) * 100))
    print('OpenCV: %s\n' % (cv2.__version__ if HAVE_CV else '★没装，flow 路线跳过★'))

    corners = [[ov[0], ov[1]], [ov[2], ov[1]], [ov[2], ov[3]], [ov[0], ov[3]]]
    cx0, cy0 = w / 2.0, h / 2.0

    cases = [(0.0, 0, 0), (0.5, 12, -6), (1.0, 25, -12),
             (2.0, 45, -20), (3.0, 70, -30), (5.0, 110, -45)]

    print('%-7s %-11s | %-28s | %s' % ('旋转', '平移', 'phase（零依赖）',
                                       'flow（OpenCV）'))
    print('%-7s %-11s | %-9s %-9s %-8s | %-9s %-9s %s'
          % ('', '', '误差', '可信块', '耗时', '误差', '特征点', '耗时'))
    print('-' * 96)
    res = {'phase': [], 'flow': []}
    for (deg, dx, dy) in cases:
        b_f = simulate(a_f, deg, dx, dy)
        b_u8 = np.clip(b_f, 0, 255).astype(np.uint8)

        rp, np_, nb, mp = run_phase(a_f, b_f, ov)
        rf, nf, nraw, mf = run_flow(a_u8, b_u8, ov)

        tA, tt = truth_matrix(deg, dx, dy, cx0, cy0)
        want = np.array(motion.apply_to_points(corners, tA, tt))

        cells = []
        for tag, r in (('phase', rp), ('flow', rf)):
            if r is None:
                cells.append((None, None))
                continue
            A, t = r
            got = np.array(motion.apply_to_points(corners, A, t))
            e = float(np.hypot(*(got - want).T).max())
            res[tag].append((deg, e))
            cells.append((e, r))

        ep = '  --  ' if cells[0][0] is None else '%6.2f' % cells[0][0]
        ef = '  --  ' if cells[1][0] is None else '%6.2f' % cells[1][0]
        print('%-7s %-11s | %-9s %-9s %-8s | %-9s %-9s %s'
              % ('%.1f°' % deg, '(%+d,%+d)' % (dx, dy),
                 ep, '%d/%d' % (np_, nb), '%.1fms' % mp,
                 ef, '%d/%d' % (nf, nraw), '%.1fms' % mf))

    print()
    for tag, name in (('phase', 'phase（分块相位相关，零依赖）'),
                      ('flow', 'flow（OpenCV 光流）')):
        v = res[tag]
        if not v:
            print('%s：没有可用的结果' % name)
            continue
        errs = [e for _d, e in v]
        print('%-28s 成功 %d/%d  误差 中位 %6.2f px  最大 %7.2f px'
              % (name, len(v), len(cases), float(np.median(errs)), max(errs)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
