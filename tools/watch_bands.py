# -*- coding: utf-8 -*-
"""采样「浮窗外围四条带」的运动 —— 判断转视角时画面到底怎么动。

★ 为什么不用"找琴面"那条路 ★
  `tools/find_panel.py` 试过：B-R 判别 + 闭运算 + 连通域，
  真正的琴面**根本没进候选**（闭运算把它和周围的暗色环境连成一片，
  长宽比当场被筛掉）。找目标这条路对抗不了游戏场景的复杂背景。

  但追踪其实**不需要找到琴** —— 只需要知道"画面整体怎么变"，
  再把那个变换应用到浮窗的四个角上。

★ 为什么采"浮窗外围的四条带" ★
  ① **必须避开浮窗**：浮窗是不动的窗口，混进去会把位移往 0 拽。
  ② **要紧贴浮窗**：相机旋转/移动时，不同深度的东西位移不同（视差）。
     贴着琴的那一圈，深度和琴最接近；画面四角的远景深度差太多。
     所以采样区取浮窗**正上 / 正下 / 正左 / 正右**四条带。

★ 判读方法 ★
  · 四条带位移**一致** → 近似纯平移，一个位移向量就够。
  · 四条带位移**不同**（尤其左右方向相反） → 含旋转，得用多个带拟合变换。
  · 全都不可信（可信度 < 4） → 转得太快，帧间内容全换了，要提帧率。

用法：
    python tools/watch_bands.py [秒数]
"""

from __future__ import annotations

import io
import json
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

DS = 3                  # 下采样倍率（在这个倍率上做相位相关）
BAND = 180              # 每条带相对浮窗向外扩多少像素


def overlay_rect(hwnd_rect):
    """浮窗在**窗口内容坐标**里的范围（按 config 的 fit_quad + 余量推算）。"""
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


def bands_around(ov, w: int, h: int):
    """浮窗外围四条带：上 / 下 / 左 / 右。返回 [(名, x, y, w, h)]。"""
    x0, y0, x1, y1 = [int(v) for v in ov]
    out = []
    # 上
    ty0, ty1 = max(0, y0 - BAND), y0
    if ty1 - ty0 >= 60:
        out.append(('上带', max(0, x0 - 200), ty0,
                    min(w, x1 + 200) - max(0, x0 - 200), ty1 - ty0))
    # 下
    by0, by1 = y1, min(h, y1 + BAND)
    if by1 - by0 >= 60:
        out.append(('下带', max(0, x0 - 200), by0,
                    min(w, x1 + 200) - max(0, x0 - 200), by1 - by0))
    # 左
    lx0, lx1 = max(0, x0 - BAND), x0
    if lx1 - lx0 >= 60:
        out.append(('左带', lx0, y0, lx1 - lx0, min(h, y1) - y0))
    # 右
    rx0, rx1 = x1, min(w, x1 + BAND)
    if rx1 - rx0 >= 60:
        out.append(('右带', rx0, y0, rx1 - rx0, min(h, y1) - y0))
    return out


def main() -> int:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    hwnd = winfocus.find_game_window()
    if not hwnd:
        print('没找到游戏窗口')
        return 1
    wr = winfocus.window_rect(hwnd)
    wx, wy, ww, wh = wr
    print('游戏窗口 rect=%s' % (wr,))
    ov = overlay_rect(wr)
    if not ov:
        print('读不到 fit_quad（或贴合没开），没法定位浮窗')
        return 1
    print('浮窗在窗口内容坐标：(%d,%d)-(%d,%d)' % tuple(int(v) for v in ov))

    bs = bands_around(ov, ww, wh)
    print('采样带：')
    for nm, bx, by, bw, bh in bs:
        print('   %s  窗口内 (%d,%d) %dx%d  (下采样后 %dx%d)'
              % (nm, bx, by, bw, bh, bw // DS, bh // DS))

    print('\n采样 %.0f 秒 —— ★ 现在请**慢慢转一下视角**（像平常那样），然后停住 ★\n'
          % secs)

    g = Grabber()
    prev = {}
    rows = []
    t0 = time.time()
    n = 0
    while time.time() - t0 < secs:
        tk = time.time()
        full = g.grab_np(wx, wy, ww, wh)
        gray = np.asarray(full[::DS, ::DS], dtype=np.float64).mean(axis=2)
        # 下采样后每个带的坐标也要跟着缩
        cur = {}
        for nm, bx, by, bw, bh in bs:
            cur[nm] = gray[by // DS:(by + bh) // DS, bx // DS:(bx + bw) // DS]
        n += 1
        if prev:
            parts, good = [], []
            for nm, *_ in bs:
                a, b = prev.get(nm), cur.get(nm)
                if a is None or b is None or min(a.shape) < 12:
                    continue
                dx, dy, c = phase(a, b)
                parts.append('%s(%+4d,%+4d)c%5.1f' % (nm, dx, dy, c))
                if c >= 4.0:
                    good.append((nm, dx, dy))
            sp = ''
            if len(good) >= 2:
                xs = [v[1] for v in good]
                ys = [v[2] for v in good]
                sp = ' 散布(%d,%d) 可信%d/%d' % (max(xs) - min(xs),
                                                 max(ys) - min(ys),
                                                 len(good), len(bs))
            rows.append((tk - t0, sp, parts))
            print('%5.2fs%s  %s' % (tk - t0, sp, '  '.join(parts)))
        prev = cur
    g.free()

    dt = time.time() - t0
    print('\n共 %d 帧 / %.1f 秒 = **%.1f fps**（抓全窗口 + 四带相位相关全算上）'
          % (n, dt, n / dt))

    withsp = [r for r in rows if '散布' in r[1]]
    print('有 ≥2 个可信带的帧：%d / %d' % (len(withsp), len(rows)))
    if withsp:
        vals = [int(r[1].split('散布(')[1].split(',')[0]) for r in withsp]
        print('这些帧里"各带位移的散布"（下采样 %dx 后的像素）：' % DS)
        print('   中位 %d  最大 %d   换算到原图：中位 %d px  最大 %d px'
              % (int(np.median(vals)), max(vals),
                 int(np.median(vals)) * DS, max(vals) * DS))
    mov = [r for r in rows if not r[1].startswith(' 散布(0,0)') and '散布' in r[1]]
    print('明显在动的帧数：%d' % len(mov))
    for (t, sp, parts) in mov[:20]:
        print('   t=%5.2f%s  %s' % (t, sp, '  '.join(parts)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
