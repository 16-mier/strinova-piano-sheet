# -*- coding: utf-8 -*-
"""卡丘琴谱器 —— 入口。

启动后有两个窗口：
  * 控制窗（本窗口有边框，用来选谱、播放、摆位置）
  * 谱面悬浮窗（无边框、置顶、鼠标穿透，压在游戏画面上）
"""

from __future__ import annotations

import os
import sys
import time
import traceback

from PyQt6.QtWidgets import QApplication

from core.paths import APP_NAME, app_dir
from ui.appstyle import apply as apply_theme
from ui.control import ControlWindow
from ui.overlay import OverlayWindow, Player


def install_crash_guard() -> None:
    """★ 让一个槽函数里的异常不再把整个程序带走 ★

    PyQt 的默认行为很凶：**槽函数（或重写的虚函数）里抛出未捕获异常，
    会走到 Qt 的 `qFatal()` → `abort()`**，整个程序当场消失，
    连个提示都没有。实测进程退出码 `0xC0000409`。

    这不是理论风险，用户已经踩过一次：点「读屏跟弹」，
    `ScreenKeyReader._tick` 里一个 `NameError: name 'time' is not defined`
    （那个模块漏了 `import time`），**程序"直接关掉了"**。

    装上 excepthook 之后，异常照样打印、照样写进 `_crash.log`
    （看得见、能查、能贴给我），但**程序继续跑**。
    同一个异常实测：装之前 abort（退出码 0xC0000409），装之后正常退出 0。

    注意这只是**兜底**，不是修好了出错的功能 —— 出错的那一步照样没做成。
    """
    def _hook(etype, value, tb):
        text = ''.join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        try:
            sys.stderr.flush()
        except Exception:
            pass
        try:
            with open(os.path.join(app_dir(), '_crash.log'), 'a',
                      encoding='utf-8') as f:
                f.write('\n===== %s =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
                f.write(text)
        except Exception:
            pass

    sys.excepthook = _hook


def main() -> int:
    install_crash_guard()

    app = QApplication(sys.argv)
    # ★ 主题要**在建任何窗口之前**装上 ★
    #   `setStyle('Fusion')` 对已经建好的控件不生效，晚一步就是
    #   "半新半旧"的观感。见 `ui/appstyle.py`。
    apply_theme(app)
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
