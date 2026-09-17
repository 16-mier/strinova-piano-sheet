# -*- coding: utf-8 -*-
"""卡丘琴谱器 —— 入口。

启动后有两个窗口：
  * 控制窗（本窗口有边框，用来选谱、播放、摆位置）
  * 谱面悬浮窗（无边框、置顶、鼠标穿透，压在游戏画面上）
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from core.paths import APP_NAME
from ui.control import ControlWindow
from ui.overlay import OverlayWindow, Player


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    player = Player()
    overlay = OverlayWindow()
    ctrl = ControlWindow(player, overlay)

    # 播放时钟 → 悬浮窗画面
    player.tick.connect(overlay.set_time)

    overlay.show()
    ctrl.show()

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
