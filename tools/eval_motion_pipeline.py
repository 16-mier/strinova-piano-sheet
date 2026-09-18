# -*- coding: utf-8 -*-
"""在**真实游戏画面**上评估"分块相位相关 + 拟合仿射"够不够用。

★ 为什么要这一步 ★
  `tests/test_motion.py` 用的是合成纹理，只能证明**算法本身没错**，
  证明不了"在真实游戏画面里够不够" —— 真实画面有一大堆水面、天空、植被、
  角色动画，这些低纹理/动态区域会让很多块不可信。

  之前那次 40 秒实采的数据里就出现过：**很多帧只有 2~3 个可信块**
  （`fit_affine` 至少要 3 个、我设的门槛是 4 个）。
  所以"可信块够不够"是这条路线能不能成立的关键，必须在真图上量。

★ 怎么做到"真实又可控" ★
  真机上拿不到 ground truth（我不知道用户当时到底转了几度）。
  所以：拿**一帧真实的游戏画面**，人工施加一个**已知的**旋转+平移，
  用它当"下一帧"。这样画面纹理是真的，变换是真值也是真的。

★ 看什么 ★
  · 可信块数量（够不够拟合）
  · **浮窗四角的跟随误差（px）** —— 这才是用户真正在意的量
  · 遮挡比例的影响（浮窗占画面多少）

用法：
    python tools/eval_motion_pipeline.py [底图] [--quick]
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image                                            # noqa: E402
from scipy import ndimage                                        # noqa: E402

from core import motion                                          # noqa: E402

# 浮窗在画面里的位置（取用户那次标定后的实际值，换算成图片坐标）
#   屏幕 fit_quad 大约 (3209,492)-(3648,1112)，游戏窗口原点 (1701,0)
OV = (3209 - 1701, 492, 3648 - 1701, 1112)
PAD = 20                        # 浮窗四周再留一点，别贴着边


def load_gray(path, scale: float = 0.5):
    """读图并转灰度 + 下采样。下采样既提速，也能让"平移近似"更自洽。"""
    im = Image.open(path).convert('L')
    w, h = im.size
    if scale != 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    return np.asarray(im, dtype=np.float64)


def simulate(img, deg: float, dx: float, dy: float):
    """造"下一帧"：先旋转（绕画面中心），再平移。

    旋转用 `ndimage.rotate`，平移用 `ndimage.shift` —— 两者都是"内容映射"：
    `out(p) = in(T⁻¹p)`，方向约定和相位相关的"b 相对 a 的位移"一致。
    """
    h, w = img.shape
    rot = ndimage.rotate(img, deg, reshape=False, order=1, mode='nearest')
    return ndimage.shift(rot, (dy, dx), order=1, mode='nearest')


def truth_matrix(deg: float, dx: float, dy: float, cx: float, cy: float):
    """真值的仿射（`d(p) = A·p + t` 形式），A = R - I。"""
    th = np.radians(deg)
    # 图像坐标 y 向下；ndimage 的 rotate 正角是逆时针，对应矩阵要取这个形式
    R = np.array([[np.cos(th), np.sin(th)], [-np.sin(th), np.cos(th)]])
    A = R - np.eye(2)
    t = np.array([dx, dy]) + np.array([cx, cy]) - R @ np.array([cx, cy])
    return A, t


def evaluate(img, deg, dx, dy, ov, cols=4, rows=3, ds=4, conf_min=None):
    h, w = img.shape
    b = simulate(img, deg, dx, dy)
    ds_img = img[::ds, ::ds]
    ds_b = b[::ds, ::ds]

    # 采样块：排除浮窗（照真实用法，浮窗是不动的窗口，混进去会把位移往 0 拽）
    hole = None
    if ov:
        hole = (ov[0] - PAD, ov[1] - PAD, ov[2] + PAD, ov[3] + PAD)

    blocks = motion.grid_blocks(w, h, cols=cols, rows=rows,
                                skip=(lambda r: motion.rects_overlap(r, hole))
                                if hole else None)
    prev, cur = {}, {}
    for (cx, cy, x0, y0, bw, bh) in blocks:
        key = (x0 // ds, y0 // ds)
        prev[key] = ds_img[y0 // ds:(y0 + bh) // ds, x0 // ds:(x0 + bw) // ds]
        cur[key] = ds_b[y0 // ds:(y0 + bh) // ds, x0 // ds:(x0 + bw) // ds]

    if conf_min is not None:
        old = motion.CONF_MIN
        motion.CONF_MIN = conf_min
    try:
        field = motion.sample_shift_field(prev, cur, ds=ds)
    finally:
        if conf_min is not None:
            motion.CONF_MIN = old

    r = motion.fit_affine(field)
    cx0, cy0 = w / 2.0, h / 2.0
    tA, tt = truth_matrix(deg, dx, dy, cx0, cy0)

    # 四角跟随误差 —— 用户真正在意的量
    if ov:
        corners = [[ov[0], ov[1]], [ov[2], ov[1]], [ov[2], ov[3]], [ov[0], ov[3]]]
    else:
        corners = [[w * 0.3, h * 0.3], [w * 0.7, h * 0.3],
                   [w * 0.7, h * 0.7], [w * 0.3, h * 0.7]]
    want = np.array(motion.apply_to_points(corners, tA, tt))
    if r is None:
        return dict(deg=deg, n=len(field), nb=len(blocks), err=None, r=None)
    A, t = r
    got = np.array(motion.apply_to_points(corners, A, t))
    err = float(np.hypot(*(got - want).T).max())
    return dict(deg=deg, n=len(field), nb=len(blocks), err=err, r=r,
                got=got, want=want)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    quick = '--quick' in sys.argv
    src = args[0] if args else os.path.join(ROOT, '_fit_source_frame.png')
    if not os.path.exists(src):
        print('找不到底图 %s' % src)
        return 1

    SCALE = 0.5                       # 全分辨率跑太慢，评估用一半
    ds = 4                            # 块内再下采样 4 倍
    img = load_gray(src, SCALE)
    h, w = img.shape
    ov = tuple(int(v * SCALE) for v in OV)
    occ = (ov[2] - ov[0]) * (ov[3] - ov[1]) / float(w * h)
    print('底图 %s → %dx%d（缩放 %.2f）' % (os.path.basename(src), w, h, SCALE))
    print('浮窗区域 %s  占画面 %.1f%% —— 这块要排除掉\n'
          % (ov, occ * 100))

    cases = [(0.0, 0, 0), (0.5, 12, -6), (1.0, 25, -12), (2.0, 45, -20)]
    if not quick:
        cases += [(3.0, 70, -30), (5.0, 110, -45)]

    print('%-8s %-10s %-10s %-14s %s' % ('旋转', '平移', '可信块/总块',
                                         '四角最大误差', '解出来的变换'))
    print('-' * 92)
    rows = []
    for (deg, dx, dy) in cases:
        for cm in ([None] if quick else [None, 3.0]):
            r = evaluate(img, deg, dx, dy, ov, cols=4, rows=3, ds=ds, conf_min=cm)
            rows.append(r)
            tag = '' if cm is None else '  (门槛%.0f)' % cm
            if r['err'] is None:
                print('%-8s %-10s %-10s %-14s %s'
                      % ('%.1f°' % deg, '(%+d,%+d)' % (dx, dy),
                         '%d/%d' % (r['n'], r['nb']), '无法拟合', '—' + tag))
                continue
            A, t = r['r']
            print('%-8s %-10s %-10s %-14s %s%s'
                  % ('%.1f°' % deg, '(%+d,%+d)' % (dx, dy),
                     '%d/%d' % (r['n'], r['nb']),
                     '%.2f px' % r['err'],
                     motion.describe(A, t), tag))

    ok = [r for r in rows if r['err'] is not None]
    print('\n能拟合的 %d/%d 组' % (len(ok), len(rows)))
    if ok:
        print('四角误差 中位 %.2f px  最大 %.2f px'
              % (float(np.median([r['err'] for r in ok])),
                 max(r['err'] for r in ok)))
        print('可信块 中位 %.1f  最少 %d（`MIN_BLOCKS` = %d）'
              % (float(np.median([r['n'] for r in ok])),
                 min(r['n'] for r in ok), motion.MIN_BLOCKS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
