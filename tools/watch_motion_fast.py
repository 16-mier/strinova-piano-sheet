# -*- coding: utf-8 -*-
"""高速运动采样：抓得快了，才第一次真正看到"画面怎么动"。

★ 和上一版的区别 ★
  `tools/watch_motion.py` 走的是老抓屏路（144ms/帧），实际采样间隔 0.23 秒。
  那个间隔下，用户转一下视角，两帧之间画面早就跑出搜索范围了 ——
  实测各块位移全是噪声（可信度 1~3），**运动过程一次都没采到**。

  `tools/probe_grab_speed.py` 把抓屏从 144.8ms 压到 59.9ms（全窗口，
  复用 DC + CreateDIBSection 直取指针）。帧间隔砍掉一半还多。

★ 为什么要分块看 ★
  如果画面是**纯平移**，每块的位移应该**完全相同**；
  若是**旋转**，离转轴越远的块位移越大、方向还不同；
  若是**透视/视差**，近处块动得多、远处动得少。
  所以"各块位移散不散"就是判断该用哪种模型的直接依据。

★ 浮窗会盖住一块，必须排除 ★
  它不动，混进去会把位移往 0 拽（16.26 就栽在这上面）。

用法：
    python tools/watch_motion_fast.py [秒数]
跑的时候请像平常那样动一下画面（转视角 / 走两步），然后停住。
"""

from __future__ import annotations

import json
import io
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import winfocus                                       # noqa: E402
from tools.probe_grab_speed import Grabber                      # noqa: E402
from tools.diag_motion_kind import phase                        # noqa: E402


def overlay_rect(hwnd_rect):
    """浮窗在游戏窗口内容坐标系里的范围（按 config 的 fit_quad + 余量推算）。"""
    try:
        cfg = json.loads(io.open(os.path.join(ROOT, 'config.json'),
                                 encoding='utf-8').read())
        q = cfg.get('fit_quad') or []
        if not cfg.get('fit_on') or len(q) != 4:
            return None
        xs = [float(p[0]) for p in q]
        ys = [float(p[1]) for p in q]
        gx, gy = hwnd_rect[0], hwnd_rect[1]
        return (min(xs) - 18 - gx, min(ys) - 80 - gy,
                max(xs) + 18 - gx, max(ys) + 52 - gy)
    except Exception:
        return None


def grid_blocks(w: int, h: int, ov, cols: int = 4, rows: int = 3):
    """把画面切成 cols×rows 块，丢掉和浮窗重叠的那些。"""
    out = []
    for r in range(rows):
        for c in range(cols):
            x0, x1 = int(w * c / cols), int(w * (c + 1) / cols)
            y0, y1 = int(h * r / rows), int(h * (r + 1) / rows)
            if ov and not (x1 < ov[0] or x0 > ov[2] or y1 < ov[1] or y0 > ov[3]):
                continue
            out.append(('第%d行第%d列' % (r + 1, c + 1), x0, y0, x1 - x0, y1 - y0))
    return out


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    hwnd = winfocus.find_game_window()
    if not hwnd:
        print('没找到游戏窗口')
        return 1
    wr = winfocus.window_rect(hwnd)
    wx, wy, ww, wh = wr
    print('游戏窗口 rect=%s' % (wr,))
    ov = overlay_rect(wr)
    print('浮窗在窗口内容坐标里的范围：%s' % (ov,))

    blocks = grid_blocks(ww, wh, ov)
    print('参与估计的块（已排除与浮窗重叠的）：')
    for nm, bx, by, bw, bh in blocks:
        print('   %-8s 窗口内 (%d, %d) %dx%d' % (nm, bx, by, bw, bh))
    print('\n采样 %.0f 秒 —— **现在请动一下画面**（转视角 / 走两步），然后停住。\n'
          % secs)

    g = Grabber()
    prev = {}
    rows = []
    t0 = time.time()
    n = 0
    while time.time() - t0 < secs:
        tk = time.time()
        # ★ 抓**一次**全窗口，再在内存里切块 ★
        #   第一版是每块各抓一次 BitBlt —— 12 次调用直接把它拖回 8fps，
        #   等于白优化抓屏。一次大 BitBlt 比十几次小的划算得多。
        full = g.grab_np(wx, wy, ww, wh)
        # 先下采样再求灰度：全分辨率转 float64 每帧要分配 58MB，很贵
        small = full[::3, ::3]
        gray_full = np.asarray(small, dtype=np.float64).mean(axis=2)
        cur = {}
        for nm, bx, by, bw, bh in blocks:
            cur[nm] = gray_full[by // 3:by // 3 + bh // 3,
                                bx // 3:bx // 3 + bw // 3]
        n += 1
        if prev:
            parts, good = [], []
            for nm, *_ in blocks:
                a = prev.get(nm)
                b = cur.get(nm)
                if a is None or b is None or min(a.shape) < 16:
                    continue
                dx, dy, c = phase(a, b)
                parts.append('%s(%+4d,%+4d)c%5.1f' % (nm, dx, dy, c))
                if c >= 4.0:
                    good.append((dx, dy))
            sp = ''
            if len(good) >= 2:
                xs = [v[0] for v in good]
                ys = [v[1] for v in good]
                sp = ' 散布(%d,%d) n=%d' % (max(xs) - min(xs), max(ys) - min(ys),
                                            len(good))
            rows.append((tk - t0, sp, parts))
            print('%5.2fs%s' % (tk - t0, sp))
            print('        %s' % '  '.join(parts))
        prev = cur
        # 不睡 —— 让它跑到抓屏的极限，看真实帧率
    g.free()

    dt = time.time() - t0
    print('\n共 %d 帧 / %.1f 秒 = **%.1f fps**（抓屏+分块相位相关全算上）'
          % (n, dt, n / dt))

    moved = [r for r in rows if 'n=' in r[1]]
    if moved:
        sp = [int(r[1].split('散布(')[1].split(',')[0]) for r in moved]
        print('有可信块参与判断的帧里，各块位移散布（下采样3倍后的像素）：')
        print('  中位 %d  最大 %d   → 若普遍很小说明近似纯平移，'
              '若明显偏大说明含旋转/透视' % (int(np.median(sp)), max(sp)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
