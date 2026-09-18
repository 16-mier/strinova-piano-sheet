# -*- coding: utf-8 -*-
"""渲染新的「自由轨道」时间轴：6 条轨道 + 等宽方块 + 上下错开。

对应用户这轮的要求：
    「不用拉长」            → 方块等宽（宽度固定，不按真实时值画）
    「不需要每个音色一个轨道」→ 不再是 16 行钢琴卷帘
    「每个声音长度一样」     → 同上
    「可以上下换轨道」       → 方块能上下拖到别的轨道（这里手工摆几个演示）

    python tools/preview_lanes.py
    → preview_lanes.png
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# ★ offscreen 下 Qt 根本不带字体，方块上的音名会渲染成一排豆腐块 ★
#   指一下系统字体目录就正常了（这只影响离屏截图，真实运行时用的是系统字体）。
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtTest import QTest                            # noqa: E402
from PyQt6.QtWidgets import QApplication                  # noqa: E402

from ui.editor import EditorDialog                        # noqa: E402
from ui.timeline_edit import LANES                        # noqa: E402

OUT = os.path.join(ROOT, 'preview_lanes.png')
# ★ 谱面要**密**才看得出自动避让 ★
#   `^` 表示 ×0.5，所以这里每个音只隔 0.5 拍，而方块宽 0.8 拍 ——
#   必然互相压上，`auto_lanes` 就会把它们摊到不同轨道。
#   （间隔 >= 0.8 拍的谱面是不重叠的，全都留在轨道 1，那是正常行为。）
SHEET = "^1 ^5 ^3 ^6' ^2' ^1&3&5 ^7' ^5 ^2 ^1'' ^4 ^6"


def main() -> int:
    app = QApplication([])
    dlg = EditorDialog()
    dlg.resize(1240, 900)
    dlg.show()
    dlg.text.setPlainText(SHEET)
    QTest.qWait(800)
    app.processEvents()

    tl = dlg.tl_edit
    tl.resize(1180, LANES * 40 + 30)
    app.processEvents()

    ns = [n for n in (tl.model.notes if tl.model else []) if not n.is_rest]
    print('音符块 %d 个（轨道是 auto_lanes 自动排的，没手动摆）' % len(ns))
    for n in ns:
        print('  块 %-7s lane=%d  start=%.2fs'
              % ('&'.join(n.pitches), n.lane, n.start * tl.spb))

    tl.update()
    app.processEvents()
    QTest.qWait(200)
    tl.grab().save(OUT)
    try:
        from PIL import Image
        im = Image.open(OUT)
        im.crop((0, 0, im.width, min(im.height, LANES * 40 + 30))).save(OUT)
    except Exception as e:
        print('裁剪失败（%s），保留原图' % e)
    print('\nOK -> %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
