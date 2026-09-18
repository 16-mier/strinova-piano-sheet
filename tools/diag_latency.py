# -*- coding: utf-8 -*-
"""测「播放时钟」和「浮窗显示」到底差多少。

用户报的：「播放延迟很高啊，都下俩个按键了显示还是上俩个」——
**差了两个音**，这是秒级的偏差，不像 UI 刷新问题。
所以先量出来，别猜。

这个脚本让播放器真的走一段，每 100 ms 记一次
`player.sec`（时钟）和 `overlay.view.sec`（浮窗拿到的），
再把**浮窗当前高亮的是第几个音**和**时钟说应该是第几个音**对一遍。

    python tools/diag_latency.py
    python tools/diag_latency.py --sheet sheets/demo.txt
"""

from __future__ import annotations

import argparse
import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtWidgets import QApplication                       # noqa: E402

from core import parser, timeline                              # noqa: E402
from core.paths import sheets_dir                              # noqa: E402
from ui.overlay import OverlayWindow, Player                   # noqa: E402
from ui.views import GridView                                  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--sheet', default=os.path.join(sheets_dir(), 'demo.txt'))
    ap.add_argument('--sec', type=float, default=3.0, help='走多久')
    ap.add_argument('--step', type=float, default=0.10)
    args = ap.parse_args()

    app = QApplication([])
    if not os.path.isfile(args.sheet):
        print('没有谱面：%s' % args.sheet)
        return 1
    sheet = parser.load(args.sheet)
    tl = timeline.Timeline(sheet)
    print('谱面 %s　%d 个音　共 %.2f 秒'
          % (os.path.basename(args.sheet), len(tl.items), tl.total_sec))
    gaps = [round(tl.items[i + 1].start_sec - tl.items[i].start_sec, 3)
            for i in range(min(6, len(tl.items) - 1))]
    print('前几个音的间隔：%s 秒' % gaps)

    player = Player()
    overlay = OverlayWindow()
    player.set_timeline(tl)
    player.tick.connect(overlay.set_time)
    view = overlay.view
    view.resize(470, 580)
    view.set_timeline(tl)
    view.preview_count = 5

    player.play()
    t_wall0 = time.monotonic()
    worst = 0.0
    rows = []
    while time.monotonic() - t_wall0 < args.sec:
        app.processEvents()
        wall = time.monotonic() - t_wall0
        ps = player.sec
        vs = view.sec
        lag = ps - vs                       # 浮窗落后时钟多少
        worst = max(worst, lag)
        # 时钟说现在该是第几个音
        i_should = tl.index_at(ps)
        grp = view._current_group()
        rows.append((wall, ps, vs, lag, i_should, len(grp)))
        time.sleep(args.step)

    print('\n%-8s %10s %10s %10s %10s' % ('墙上时间', '时钟秒', '浮窗秒', '落后', '该第几个音'))
    print('-' * 56)
    for wall, ps, vs, lag, i_should, _n in rows:
        print('%-8.2f %10.3f %10.3f %10.4f %10d'
              % (wall, ps, vs, lag, i_should + 1))
    print('-' * 56)
    print('最大落后 %.4f 秒' % worst)
    if worst < 0.05:
        print('✅ 时钟和浮窗是同步的 —— 延迟不在这一段')
    elif worst < 0.20:
        print('⚠ 有小幅落后（%.0f ms）—— 看下面"该第几个音"对不对得上'
              % (worst * 1000))
    else:
        print('❌ 浮窗明显落后时钟 %.2f 秒 —— 这就是用户看到的现象' % worst)

    # 顺便验"浮窗高亮的是不是时钟说的那个音"
    player.seek(0.0)
    app.processEvents()
    bad = 0
    for want_i, item in enumerate(tl.items[:6]):
        player.seek(item.start_sec + 0.01)
        app.processEvents()
        grp = view._current_group()
        got = tl.index_at(view.sec)
        if got != want_i:
            bad += 1
            print('  第 %d 个音处：时钟说 %d，浮窗算成 %d'
                  % (want_i + 1, want_i, got))
    if bad == 0:
        print('✅ 浮窗高亮的音和时钟一致（抽查前 6 个）')
    else:
        print('❌ 有 %d 处对不上' % bad)
    overlay.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
