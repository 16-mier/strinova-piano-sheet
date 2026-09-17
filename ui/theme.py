# -*- coding: utf-8 -*-
"""配色与尺寸 —— 悬浮在游戏画面上，必须够醒目、又不能太挡视线。"""

from PyQt6.QtGui import QColor

# 面板背景（半透明深色）
BG = QColor(10, 12, 18, 168)
BG_EDGE = QColor(96, 106, 138, 205)

# 空闲格子
CELL = QColor(46, 52, 70, 200)
CELL_EDGE = QColor(98, 108, 140, 215)

# 当前该打的键 —— 亮黄，一眼就看到
ACTIVE = QColor(255, 206, 48)
ACTIVE_EDGE = QColor(255, 255, 255)
ACTIVE_TEXT = QColor(28, 22, 0)

# 后续音符 —— 越远越淡
UPCOMING = [
    QColor(255, 238, 158, 215),
    QColor(222, 212, 152, 180),
    QColor(192, 188, 152, 150),
    QColor(164, 164, 152, 122),
    QColor(146, 150, 154, 100),
]

TEXT = QColor(234, 238, 248)
TEXT_DIM = QColor(154, 162, 182)

# 音区配色（下落视图 / 预览条用）
ZONE_COLORS = [
    QColor(120, 200, 255),   # 中音区
    QColor(140, 235, 190),   # 中音高段
    QColor(255, 190, 120),   # 高音区
    QColor(240, 150, 220),   # 倍高音
]

RADIUS = 10
GAP = 6


def zone_of(row: int) -> int:
    """格子所在行 -> 音区序号（0 最下）。"""
    return max(0, min(3, row))
