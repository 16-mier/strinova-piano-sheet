# -*- coding: utf-8 -*-
"""把浮窗的网格渲染成 PNG —— 用来肉眼确认"连按同一个键"看不看得出来。

用户报的：「同一个按键需要按下两次的时候显示不明显」。
修完之后要能一眼看出差别，所以这里直接出图对比。

    5 5 5          同一个键连按三下
    5 5 3 5        （下面是几个别的场景）

用法：
    python tools/preview_double.py
    python tools/preview_double.py --out _shot.png
"""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtCore import Qt                                    # noqa: E402
from PyQt6.QtGui import QPixmap                                # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from core import parser, timeline                              # noqa: E402
from ui.views import GridView                                  # noqa: E402

W, H = 470, 580


def shot(app, text: str, sec: float, out: str, title: str = ''):
    tl = timeline.Timeline(parser.parse(text))
    v = GridView()
    v.resize(W, H)
    v.set_timeline(tl)
    v.preview_count = 5
    v.set_time(sec)
    app.processEvents()
    pm = QPixmap(W, H)
    pm.fill(Qt.GlobalColor.transparent)
    v.render(pm)
    pm.save(out)
    print('%-38s -> %s' % (title or text.replace('\n', ' '), out))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    app = QApplication([])

    cases = [
        ('5 5 5 连按三下', '0:5\n0.5:5\n1:5\n1.5:5\n2:5\n', 0.1),
        ('5 5 3 5 隔一个再来', '0:5\n0.5:5\n1:3\n1.5:5\n', 0.1),
        # ★ 这一条验的是"重复的键**不在**当前格" ★
        #   当前格是 5，而 3 在第 2、4 个音各来一次 ——
        #   它的角标应该写 `2·4`，而不是只写一个 `2`。
        ('5 3 5 3 后续格标多序号', '0:5\n0.5:3\n1:5\n1.5:3\n', 0.1),
        ('3 5 3 当前格重复＋后续重复', '0:3\n0.5:5\n1:3\n1.5:5\n', 0.1),
        ('和弦 1&5 然后 5', '0:1&5\n0.8:5\n', 0.1),
        ('普通单音（对照）', '0:5\n0.6:3\n1.2:7\n', 0.1),
    ]
    outs = []
    for i, (name, text, sec) in enumerate(cases):
        out = args.out or os.path.join(ROOT, '_prev_%d.png' % i)
        outs.append(shot(app, text, sec, out, name))
    print('\n生成：%s' % ' '.join(os.path.basename(o) for o in outs))
    return 0


if __name__ == '__main__':
    sys.exit(main())
