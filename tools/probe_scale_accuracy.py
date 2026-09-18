# -*- coding: utf-8 -*-
"""探针：光流估出来的**尺度**准不准 —— 「琴远了网格变小、近了变大」靠的就是它。

★ 为什么要单独量这个 ★
  用户的原话：「让这个铺面跟着游戏内的琴的画面走，**会自动放大缩小**，
  贴在琴的按键上面」。

  平移和旋转都验过了（中位误差 0.02px），但**尺度一直没验**：
  `estimateAffinePartial2D` 是相似变换（平移+旋转+缩放），理论上带尺度，
  可它在真实游戏画面上的尺度精度是多少，没人量过。
  而 `ui/track.py` 的 `healthy()` 当初只查了旋转角、平移量、内点比例 ——
  **尺度是漏的**。尺度估错时四角会整体胀大或缩扁，而
  `fit.is_usable()` 只查凸性和面积，一个"变大了 3 倍"的网格照样能过。

★ 怎么造"琴变近了" ★
  以画面中心为原点做缩放（`cv2.warpAffine` + 尺度矩阵），
  这就是"相机往前推"在屏幕上的样子。真值尺度是已知的。

用法：
    python tools/probe_scale_accuracy.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image                                            # noqa: E402

from core import flow, motion                                    # noqa: E402

OV = (3209 - 1701, 492, 3648 - 201, 1112)      # 浮窗区域（图片坐标）


def load_u8(path, scale=0.5):
    im = Image.open(path).convert('L')
    w, h = im.size
    if scale != 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    return np.asarray(im, dtype=np.uint8)


def zoom_about_centre(img, s: float):
    """以画面中心为原点缩放 —— 模拟相机推近/拉远。

    返回 `(新图, 真值变换(A, t))`，用的是本项目 `d(p) = A·p + t` 的形式。
    """
    import cv2
    h, w = img.shape
    cx, cy = w / 2.0, h / 2.0
    # 内容映射：p' = s·(p - c) + c = s·p + (1-s)·c
    M = np.array([[s, 0.0, (1 - s) * cx], [0.0, s, (1 - s) * cy]])
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    A = M[:, :2] - np.eye(2)          # 位移形式 d(p) = A·p + t
    t = M[:, 2]
    return out, (A, t)


def main() -> int:
    if not flow.available():
        print('没装 opencv-python')
        return 1
    src = os.path.join(ROOT, '_fit_source_frame.png')
    if not os.path.exists(src):
        print('找不到底图 %s' % src)
        return 1

    SCALE = 0.5
    a = load_u8(src, SCALE)
    h, w = a.shape
    print('底图 %dx%d（缩放 %.2f）' % (w, h, SCALE))

    ov = tuple(int(v * SCALE) for v in OV)
    quad = [[ov[0], ov[1]], [ov[2], ov[1]], [ov[2], ov[3]], [ov[0], ov[3]]]
    print('浮窗四边形 %s\n' % quad)

    print('%-9s %-11s %-12s %-14s %s'
          % ('真值尺度', '估出尺度', '尺度误差', '四角最大误差', '内点'))
    print('-' * 74)
    rows = []
    for s in (0.65, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0):
        b, (tA, tt) = zoom_about_centre(a, s)
        mask = flow.make_mask(a.shape, exclude=ov)
        A, t, info = flow.flow_affine(a, b, mask=mask)
        if A is None:
            print('%-9.2f %-11s %-12s %-14s %s'
                  % (s, '--', '--', '失败', info.get('error', '')))
            continue
        got = motion.scale_of(A)
        want = np.array(motion.apply_to_points(quad, tA, tt))
        have = np.array(motion.apply_to_points(quad, A, t))
        err = float(np.hypot(*(have - want).T).max())
        rows.append((s, got, err, info.get('n_inlier', 0)))
        print('%-9.2f %-11.4f %+-12.4f %-14.2f %d'
              % (s, got, got - s, err, info.get('n_inlier', 0)))

    if rows:
        errs = [r[2] for r in rows]
        serr = [abs(r[1] - r[0]) for r in rows]
        print('\n尺度绝对误差 中位 %.4f  最大 %.4f' % (float(np.median(serr)), max(serr)))
        print('四角最大误差 中位 %.2f px  最大 %.2f px'
              % (float(np.median(errs)), max(errs)))
        worst = max(rows, key=lambda r: r[2])
        print('最差的一档：尺度 %.2f → 四角差 %.1f px' % (worst[0], worst[2]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
