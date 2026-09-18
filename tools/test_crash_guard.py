# -*- coding: utf-8 -*-
"""验证：Qt 槽函数里抛异常时，自定义 sys.excepthook 能不能保住程序。

背景：点「读屏跟弹」时 `ScreenKeyReader._tick` 抛了 NameError，
      **整个程序直接关掉了**（PyQt 默认会调 qFatal → abort）。
      这个实验回答"加 excepthook 到底管不管用"。
"""

import os
import sys
import traceback

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtCore import QTimer                         # noqa: E402
from PyQt6.QtWidgets import QApplication                # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else 'hook'

if MODE == 'hook':
    def _hook(t, v, tb):
        print('[excepthook] 抓到 %s: %s' % (t.__name__, v), flush=True)
        traceback.print_exception(t, v, tb)
    sys.excepthook = _hook
    print('模式：装了自定义 excepthook')

app = QApplication([])


def boom():
    raise RuntimeError('槽函数里炸了')


QTimer.singleShot(30, boom)
QTimer.singleShot(400, app.quit)
app.exec()
print('★ 程序活到了最后（没有 abort）')
