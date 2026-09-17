# -*- coding: utf-8 -*-
"""离线渲染预览 —— 不开窗口，直接把网格视图在不同时刻画成图片检查效果。

用法：python tools/preview.py
输出：preview_grid_*.png
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt                      # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication          # noqa: E402

from core import parser, timeline                 # noqa: E402
from ui.views import GridView                     # noqa: E402

# 模拟"游戏画面"的底色，方便看清半透明面板
FAKE_GAME = QColor(58, 68, 88)


def composite(widget, bg=FAKE_GAME) -> QPixmap:
    pm = widget.grab()
    out = QPixmap(pm.size())
    out.fill(bg)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def main() -> int:
    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    sheet_path = os.path.join(ROOT, 'sheets', 'demo.txt')
    sheet = parser.load(sheet_path)
    tl = timeline.Timeline(sheet)
    print('谱面: %s' % sheet.title)
    print('统计: %s' % tl.stats())

    times = [0.0, 3.0, 12.0]

    grid = GridView()
    grid.resize(470, 580)
    grid.set_timeline(tl)

    for t in times:
        grid.set_time(t)
        cur = tl.item_at(t)
        print('t=%5.1fs  网格 <= %s' % (t, cur.chord if cur else '—'))
        composite(grid).save(os.path.join(ROOT, 'preview_grid_%.0f.png' % t))

    print('OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
