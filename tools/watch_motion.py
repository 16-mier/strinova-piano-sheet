# -*- coding: utf-8 -*-
"""实况诊断：一边抓一边算 —— 你动，我看画面到底怎么动。

★ 为什么必须"实况" ★
  `walk_0~4.png` 的间隔是 4 秒。结果就是：你真在动的那一下就 4 秒里的一瞬，
  抓到的两帧之间**跨过了整段运动**（各块位移差到 95~105px，可信度掉到 1.5），
  剩下三帧你已经站住了（位移恒 0，可信度 7~85）。
  **运动过程本身一次都没被采到。**

  这个脚本以 ~0.15 秒的间隔连抓，并且**每一帧都报分区位移**，
  所以「动的瞬间」不会漏。

用法：
    python tools/watch_motion.py [秒数]
然后在它跑的时候，像平常那样动一下视角 / 走两步。
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
from tools.diag_motion_kind import ZONES, cut, gray, phase      # noqa: E402


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 14.0
    hwnd = winfocus.find_game_window()
    if not hwnd:
        print('没找到游戏窗口')
        return 1
    print('游戏 hwnd=%s' % hwnd)
    print('采样 %.0f 秒。**现在请像平常那样动一下画面**（转视角 / 走两步），'
          '然后停住。\n' % secs)

    # ★ 采样块必须避开浮窗 ★
    #   浮窗是**不动的窗口**，把包含它的块拿去算位移，结果会被它往 0 拽
    #   （16.26 里就是这么量出一个假的"0 位移"的）。
    #   这里不枚举窗口（ctypes 回调那次调用报 WinFunctionType 冲突，不值当为它绕），
    #   直接用 `config.json` 里刚标定好的 `fit_quad` 把浮窗范围推算出来。
    wr = winfocus.window_rect(hwnd)
    print('游戏窗口 rect=%s' % (wr,))
    ov = overlay_rect_in_game(wr)
    if ov:
        print('浮窗在截图里的范围大约 x %d~%d  y %d~%d（按 fit_quad + 余量推算）'
              % ov)
    else:
        print('（没读到 fit_quad，不做遮挡排除）')
    print()

    zones = dict(ZONES)
    if ov:
        keep = {}
        for name, box in zones.items():
            if not box_overlaps_cut(box, ov, img_size=(3429, 2128)):
                keep[name] = box
        dropped = [n for n in zones if n not in keep]
        if dropped:
            print('避开浮窗，丢掉这些块：%s' % '、'.join(dropped))
        if len(keep) >= 2:
            zones = keep
    print('参与位移估计的块：%s\n' % '、'.join(zones))

    prev = None
    rows = []
    t0 = time.time()
    n = 0
    while time.time() - t0 < secs:
        tk = time.time()
        img, msg = grab_desktop(hwnd)
        if img is None:
            print('抓取失败：%s' % msg)
            return 1
        g = gray_of(img)
        n += 1
        if prev is not None:
            gx, gy, gc = phase(prev, g)
            parts = []
            best = []
            for name, box in zones.items():
                dx, dy, c = phase(cut(prev, box), cut(g, box))
                parts.append('%s(%+3d,%+3d)c%4.1f' % (name.split()[0], dx, dy, c))
                if c >= 3.0:
                    best.append((dx, dy))
            spread = ''
            if len(best) >= 2:
                xs = [v[0] for v in best]
                ys = [v[1] for v in best]
                spread = ' 散布(%d,%d)' % (max(xs) - min(xs), max(ys) - min(ys))
            rows.append((tk - t0, gx, gy, gc, spread, parts))
            print('%5.2fs 全(%+4d,%+4d)c%6.1f%s | %s'
                  % (tk - t0, gx, gy, gc, spread, ' '.join(parts)))
        prev = g
        dt = 0.15 - (time.time() - tk)
        if dt > 0:
            time.sleep(dt)

    print('\n共 %d 帧。' % n)
    move = [r for r in rows if abs(r[1]) + abs(r[2]) > 0]
    print('全画面位移非零的帧：%d / %d' % (len(move), len(rows)))
    if move:
        print('这些帧里的位移与散布：')
        for (t, gx, gy, gc, sp, _) in move:
            print('  t=%5.2f  全(%+4d,%+4d)c%6.1f%s' % (t, gx, gy, gc, sp))
    return 0


def gray_of(img, scale: float = 0.20):
    """抓到的 PIL 图 → 下采样灰度（和 diagnose 那边同一套参数）。"""
    from PIL import Image
    w, h = img.size
    g = img.convert('L').resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    return np.asarray(g, dtype=np.float64)


def overlay_rect_in_game(hwnd_rect):
    """按 `config.json` 的 `fit_quad` 推算浮窗在**截图**里的范围 (x0,y0,x1,y1)。

    窗口几何是 `ui/overlay.py::_apply_fit_geometry` 算的：
    `bbox(quad)` 左右各留 18，上方再多留 62、下方再多留 34
    （顶部大字和底部红框会被透视外推到四边形之外）。
    这里按同一套边距还原，够用来判断"哪个采样块被盖住了"。
    """
    import io
    import json
    try:
        cfg = json.loads(io.open(os.path.join(ROOT, 'config.json'),
                                 encoding='utf-8').read())
        q = cfg.get('fit_quad') or []
        if not cfg.get('fit_on') or len(q) != 4:
            return None
        xs = [float(p[0]) for p in q]
        ys = [float(p[1]) for p in q]
        x0, x1 = min(xs) - 18, max(xs) + 18
        y0, y1 = min(ys) - 80, max(ys) + 52
        gx, gy = hwnd_rect[0], hwnd_rect[1]
        return (x0 - gx, y0 - gy, x1 - gx, y1 - gy)
    except Exception:
        return None


def box_overlaps_cut(box, rect, img_size):
    """比例采样框和像素矩形（截图坐标）有没有重叠。"""
    iw, ih = img_size
    bx0, by0 = box[0] * iw, box[1] * ih
    bx1, by1 = box[2] * iw, box[3] * ih
    rx0, ry0, rx1, ry1 = rect
    return not (bx1 < rx0 or bx0 > rx1 or by1 < ry0 or by0 > ry1)


if __name__ == '__main__':
    sys.exit(main())
