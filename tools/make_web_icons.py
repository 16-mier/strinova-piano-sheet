# -*- coding: utf-8 -*-
"""生成手机版的 App 图标（PWA manifest 和 Android 打包都要用）。

★ 为什么用 Qt 画、不用现成图片 ★
  项目里本来就有一套"运行时用 QPainter 画图标"的做法
  （`ui/appstyle.py`），理由是不赌系统字体、也不往仓库里塞二进制。
  这里沿用同一套：用 QPainter 画出来存成 PNG，风格跟桌面版一致。

产物：`web/icons/icon-192.png` / `icon-512.png`
      `web/icons/icon-maskable-512.png`（Android 自适应图标要留安全边距）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from PyQt6.QtCore import QPointF, QRectF, Qt                   # noqa: E402
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient,  # noqa: E402
                         QPainter, QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import QApplication                       # noqa: E402

# 跟 `web/css/app.css` 里那套变量一致
BG_TOP = QColor(26, 31, 44)
BG_BOT = QColor(13, 16, 23)
ACCENT = QColor(70, 201, 168)
ACCENT_HI = QColor(95, 224, 189)
TEXT = QColor(230, 235, 245)
EDGE = QColor(58, 69, 96)


def draw_icon(size: int, pad_ratio: float = 0.0) -> QPixmap:
    """画一张图标。`pad_ratio` 是给 Android 自适应图标留的安全边距比例。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    pad = size * pad_ratio
    box = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)

    # ---- 底：圆角方块 + 竖向渐变 ----
    grad = QLinearGradient(box.topLeft(), box.bottomLeft())
    grad.setColorAt(0.0, BG_TOP)
    grad.setColorAt(1.0, BG_BOT)
    p.setPen(QPen(EDGE, max(1.0, box.width() * 0.012)))
    p.setBrush(QBrush(grad))
    r = box.width() * 0.22
    p.drawRoundedRect(box, r, r)

    # ---- 中间：2×2 的格子（这台琴的抽象）----
    inner = box.adjusted(box.width() * 0.20, box.height() * 0.20,
                         -box.width() * 0.20, -box.height() * 0.20)
    gap = inner.width() * 0.10
    cell = (inner.width() - gap) / 2.0
    radius = cell * 0.22

    # 左上那一格点亮（"当前该弹的"），跟练琴页的主视觉呼应
    for row in (0, 1):
        for col in (0, 1):
            x = inner.left() + col * (cell + gap)
            y = inner.top() + row * (cell + gap)
            rr = QRectF(x, y, cell, cell)
            if row == 0 and col == 0:
                p.setBrush(QBrush(QColor(96, 108, 74, 245)))
                p.setPen(QPen(QColor(186, 202, 150), max(2.0, cell * 0.09)))
            else:
                p.setBrush(QBrush(QColor(46, 52, 70, 235)))
                p.setPen(QPen(QColor(98, 108, 140, 200), max(1.0, cell * 0.05)))
            p.drawRoundedRect(rr, radius, radius)

    # ---- 左下角一颗青绿音符（点题的） ----
    note_cx = inner.left() + cell * 0.50
    note_cy = inner.top() + cell + gap + cell * 0.62
    head_r = cell * 0.20
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ACCENT_HI)
    p.drawEllipse(QPointF(note_cx, note_cy), head_r, head_r * 0.82)
    pen = QPen(ACCENT_HI)
    pen.setWidthF(max(2.0, cell * 0.085))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(note_cx + head_r * 0.94, note_cy),
               QPointF(note_cx + head_r * 0.94, note_cy - cell * 0.78))
    p.drawPolyline([
        QPointF(note_cx + head_r * 0.94, note_cy - cell * 0.78),
        QPointF(note_cx + head_r * 0.94 + cell * 0.32, note_cy - cell * 0.62),
    ])

    # ---- 右下角：三条横线（"谱面"） ----
    pen2 = QPen(ACCENT)
    pen2.setWidthF(max(1.5, cell * 0.055))
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    lx = inner.left() + cell + gap + cell * 0.20
    for i, ly in enumerate((0.34, 0.52, 0.70)):
        w = cell * (0.60 if i < 2 else 0.38)
        y = inner.top() + cell + gap + cell * ly
        p.drawLine(QPointF(lx, y), QPointF(lx + w, y))

    p.end()
    return pm


def main() -> int:
    out = os.path.join(ROOT, 'web', 'icons')
    os.makedirs(out, exist_ok=True)

    app = QApplication.instance() or QApplication([])          # noqa: F841

    # 普通图标：铺满
    for size in (192, 512):
        p = os.path.join(out, 'icon-%d.png' % size)
        draw_icon(size).save(p, 'PNG')
        print('  %-34s %d 字节' % ('icons/icon-%d.png' % size,
                                   os.path.getsize(p)))

    # Android 自适应图标：外圈会被系统裁掉，所以内容要往里缩
    p = os.path.join(out, 'icon-maskable-512.png')
    draw_icon(512, pad_ratio=0.14).save(p, 'PNG')
    print('  %-34s %d 字节' % ('icons/icon-maskable-512.png',
                               os.path.getsize(p)))
    print('【图标生成完毕】')
    return 0


if __name__ == '__main__':
    sys.exit(main())
