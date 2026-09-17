# -*- coding: utf-8 -*-
"""离线渲染打谱器界面（不开主程序，直接截图检查）。

用法：python tools/preview_editor.py
输出：preview_editor.png
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QTimer               # noqa: E402
from PyQt6.QtWidgets import QApplication      # noqa: E402

from ui.editor import EditorDialog            # noqa: E402

OUT = os.path.join(ROOT, 'preview_editor.png')
SHEET = os.path.join(ROOT, 'sheets', 'demo.txt')


def main() -> int:
    app = QApplication(sys.argv)
    dlg = EditorDialog(SHEET)
    dlg.resize(1240, 840)
    dlg.show()

    def shoot():
        dlg.grab().save(OUT)
        print('OK -> %s' % OUT)
        print('音符数: %s' % (len(dlg.model.notes) if dlg.model else 0))
        app.quit()

    QTimer.singleShot(1800, shoot)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
