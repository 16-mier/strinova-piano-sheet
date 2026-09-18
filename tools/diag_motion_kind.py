# -*- coding: utf-8 -*-
"""诊断：真实画面移动到底是「平移」还是「旋转/透视」—— 决定追踪能用多简单的模型。

★ 为什么不再用模拟 ★
  16.26 里是拿一帧静止图**自己转 0.5°**去测的，那只能说明"纯平移模型
  对旋转无能为力"，**不能说明用户实际会遭遇什么运动**。
  用户报「画面移动没有贴合」之后，得看他**真的动起来**是什么样。

  手上正好有 5 帧用户走动时抓的真画面（`walk_0~4.png`，间隔 4 秒）。

★ 判据：分区位移是否一致 ★
  · **纯平移**：画面每个角落的位移**完全相同**。
  · **旋转**：离旋转中心越远，位移越大，且方向垂直于半径 ——
    四个象限的位移向量会**明显不同**。
  · **透视/视差**：近处物体动得多、远处动得少 —— 也表现为分区不一致。

  所以只要把画面切成几块分别做相位相关，看这些位移向量散不散，就知道了。

用法：
    python tools/diag_motion_kind.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PIL import Image                                            # noqa: E402

TEMP = os.environ.get('TEMP', '.')
SCALE = 0.20                    # 下采样倍率；1/0.2 = 5px 原图 / 下采样像素


def gray(path):
    im = Image.open(path).convert('L')
    w, h = im.size
    im = im.resize((int(w * SCALE), int(h * SCALE)), Image.BILINEAR)
    return np.asarray(im, dtype=np.float64)


def phase(a: np.ndarray, b: np.ndarray, win: bool = True):
    """相位相关。返回 (dx, dy, 可信度)。

    `win=True` 时先加汉宁窗再变换 —— 不加窗的话，
    图像边界处的突变会在互功率谱里形成十字亮线，把真峰压掉。
    处理"两帧内容不完全重叠"（移动后边缘露出新内容）时这点很关键。
    """
    if win:
        wy = np.hanning(a.shape[0])[:, None]
        wx = np.hanning(a.shape[1])[None, :]
        w = wy * wx
        a = (a - a.mean()) * w
        b = (b - b.mean()) * w
    else:
        a = a - a.mean()
        b = b - b.mean()
    fa = np.fft.rfft2(a)
    fb = np.fft.rfft2(b)
    cross = fa * np.conj(fb)
    mag = np.abs(cross)
    mag[mag < 1e-12] = 1e-12
    cross /= mag
    cc = np.fft.irfft2(cross, s=a.shape)
    cc = np.fft.fftshift(cc)
    cy, cx = cc.shape[0] // 2, cc.shape[1] // 2
    pk0 = int(np.argmax(cc))
    py, px = np.unravel_index(pk0, cc.shape)
    peak = float(cc[py, px])
    cc2 = cc.copy()
    h, w2 = cc.shape
    r = 5
    for j in range(-r, r + 1):
        for i in range(-r, r + 1):
            cc2[(py + j) % h, (px + i) % w2] = -1e9
    second = float(cc2.max())
    return px - cx, py - cy, (peak / second if second > 0 else 999.0)


# 五个采样区（比例坐标）。名字说明它在画面里的位置。
ZONES = {
    '左上 1/4': (0.02, 0.10, 0.48, 0.48),
    '右上 1/4': (0.52, 0.10, 0.98, 0.48),
    '左下 1/4': (0.02, 0.52, 0.48, 0.92),
    '右下 1/4': (0.52, 0.52, 0.98, 0.92),
    '正中一块': (0.28, 0.28, 0.72, 0.72),
}


def cut(a, box):
    h, w = a.shape
    return a[int(h * box[1]):int(h * box[3]), int(w * box[0]):int(w * box[2])]


def main() -> int:
    paths = [os.path.join(TEMP, 'walk_%d.png' % i) for i in range(5)]
    paths = [p for p in paths if os.path.exists(p)]
    if len(paths) < 2:
        print('需要至少两帧 walk_*.png，现在只有 %d 帧' % len(paths))
        return 1
    print('用 %d 帧真实走动画面，下采样 %.2f（1px 下采样 = %.0fpx 原图）\n'
          % (len(paths), SCALE, 1 / SCALE))

    frames = [gray(p) for p in paths]
    print('帧尺寸 %s\n' % (frames[0].shape,))

    for k in range(len(frames) - 1):
        a, b = frames[k], frames[k + 1]
        print('══ 第 %d → %d 帧（间隔 4 秒）══' % (k, k + 1))
        gx, gy, gc = phase(a, b)
        print('  全画面      (%+5d, %+5d)  可信 %7.2f' % (gx, gy, gc))
        vecs = []
        for name, box in ZONES.items():
            dx, dy, c = phase(cut(a, box), cut(b, box))
            vecs.append((name, dx, dy, c))
            print('  %-10s (%+5d, %+5d)  可信 %7.2f' % (name, dx, dy, c))

        good = [(n, x, y) for n, x, y, c in vecs if c >= 3.0]
        if len(good) >= 2:
            xs = np.array([v[1] for v in good], dtype=float)
            ys = np.array([v[2] for v in good], dtype=float)
            print('  → 可信区域的位移散布：x 极差 %.1f  y 极差 %.1f  '
                  '(下采样px) = %.0f / %.0f 原图px'
                  % (xs.max() - xs.min(), ys.max() - ys.min(),
                     (xs.max() - xs.min()) / SCALE,
                     (ys.max() - ys.min()) / SCALE))
            if xs.max() - xs.min() <= 1 and ys.max() - ys.min() <= 1:
                print('  → 各块位移一致 ⇒ **近似纯平移**，相位相关够用')
            else:
                print('  → 各块位移明显不同 ⇒ **含旋转/透视**，'
                      '单一位移向量跟不住（要靠多个块拟合仿射）')
        else:
            print('  → 可信区域不足 %d 个，这次判断不了' % len(good))
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
