# -*- coding: utf-8 -*-
"""面板 —— Premiere 那种「标题栏横贯整条，内容区在下面」的分区。

★ 为什么不用 `QGroupBox` ★

  Qt 的 `QGroupBox` 是"文字骑在边框线上"的传统样子：
  标题靠 `subcontrol-origin: margin` 浮在边框那里，**宽度只到文字**，
  两边露出边框。那是 1990 年代对话框的审美。

  而 Premiere / 达芬奇那类剪辑软件的面板是**分区**的：
  顶上一条实心的标题栏横贯整条，内容明确地待在下面那块里 ——
  用户要的就是那种分区感（原话「可以和PR的轨道一样啊」，
  时间轴那边早就是这么做的了）。

  ★ QSS 做不到这件事 ★
    `QGroupBox::title` 是个子控件，它只能撑到文字宽度 ——
    没有 `width: 100%` 这种东西。想把标题条横贯整条，
    只能自己用 `QFrame` + `QLabel` 搭。

★ 用法 ★
    box = PanelBox('时间轴')
    box.inner.addWidget(self.tl_scroll, 1)     # 注意是 `.inner`，不是 layout()

  （不覆盖 `layout()`：那是 Qt 的 C++ 方法，PyQt 里覆盖了也未必被
    内部调用路径认，不如给个明确的名字。）
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class PanelBox(QFrame):
    """一个带标题条的分区容器。对外只需要 `inner` 和 `setTitle()`。"""

    def __init__(self, title: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('panel')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title = QLabel(title)
        self._title.setObjectName('panelTitle')
        outer.addWidget(self._title)

        # 内容区单独包一层：标题条贴着圆角，内容区要留内边距，
        # 两者的边距不一样，塞在同一个 layout 里会互相将就。
        body = QWidget()
        body.setObjectName('panelBody')
        self.inner = QVBoxLayout(body)
        # 内容区的内边距在这里统一给 —— 调用方不用每处再设一遍，
        # 也就不会出现"这个面板 14px、那个 12px"。
        self.inner.setContentsMargins(12, 12, 12, 12)
        self.inner.setSpacing(8)
        outer.addWidget(body, 1)

    def setTitle(self, text: str):
        self._title.setText(text)

    def title(self) -> str:
        return self._title.text()
