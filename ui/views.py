# -*- coding: utf-8 -*-
"""谱面显示 —— 4×4 网格高亮式。

GridView  跟游戏里那台琴的排列一模一样；当前该打的键亮黄，
          后面几个音按远近依次变淡（个数可调），底部再列一遍音名。

（下落式 FallView 已经在 v1.1 砍掉：实战里 4×4 网格更好认。）
"""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core import layout
from core.timeline import Timeline

from . import theme as T


def _pf(pitch: str) -> float:
    """键名 -> 频率（判断"是不是同一个音高"用）。"""
    from core.transcribe import pitch_freq
    try:
        return pitch_freq(pitch)
    except Exception:
        return 1.0


class SheetView(QWidget):
    """两种视图的公共部分。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.sec = 0.0
        self.preview_count = 5
        self.show_labels = True
        self.bg_scale = 1.0              # 底板浓度（1 = 原样，0 = 全透明）
        self.hidden = False              # 临时隐身：什么都不画
        self.flash: dict[str, float] = {}   # 实时跟弹：音高 -> 到期时刻
        self._last_key = None            # 上一帧的绘制内容指纹（用于省重绘）
        # ★ 这里**不要**再设 WA_TranslucentBackground ★
        #   父窗口（OverlayWindow）已经设过了，子控件再设一次会把自己
        #   变成**原生窗口**，于是鼠标消息被它自己接走、不再冒泡到父窗口 ——
        #   表现就是「点了允许拖动却怎么都拖不动」。
        #   QWidget 默认本来就不画背景，透明效果不受影响。

    # ---- 数据接口 ----

    def set_timeline(self, tl: Timeline | None):
        self.timeline = tl
        self._last_key = None
        self.update()

    def set_time(self, sec: float):
        self.sec = sec
        self.update()

    # ---- 工具 ----

    def _panel(self, p: QPainter):
        w, h = self.width(), self.height()
        p.setPen(QPen(self._dim(T.BG_EDGE), 2))
        p.setBrush(QBrush(self._dim(T.BG)))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 16, 16)

    def set_bg_scale(self, scale: float):
        """底板浓度 —— 只影响背景和格子，音名文字一点不受影响。"""
        self.bg_scale = max(0.0, min(1.0, float(scale)))
        self._last_key = None
        self.update()

    def set_hidden(self, on: bool):
        """什么都不画 —— 配合 WA_TranslucentBackground 就等于完全透明。

        ★ 别用 setWindowOpacity(0) 干这事 ★
          那会去动窗口的 WS_EX_LAYERED 属性，跟"鼠标穿透"那套
          exstyle 管理打架 —— 实测勾上「允许拖动」后浮窗会变成一片白。
        """
        self.hidden = bool(on)
        self._last_key = None
        self.update()

    def _dim(self, c: QColor) -> QColor:
        """按底板浓度把颜色调淡（bg_scale = 1 时原样返回）。"""
        if self.bg_scale >= 0.999:
            return c
        out = QColor(c)
        out.setAlpha(int(round(c.alpha() * self.bg_scale)))
        return out

    # ---- 实时跟弹的高亮 ----

    def set_flash(self, pitch: str, seconds: float = 0.35):
        """让某个键亮一下 —— 游戏里敲了哪个就亮哪个。

        ★ 顺便把**同音高的另一个键**的残留高亮清掉 ★
          `8` 和 `1'` 是同一个音高：上一个亮的是 `1'`、这次识别成 `8`，
          两个格子会同时亮着，看着就像"按错了"。
        """
        if not pitch:
            return
        for other in list(self.flash):
            if other != pitch and abs(1200.0 * math.log2(
                    _pf(other) / _pf(pitch))) < 30.0:
                del self.flash[other]
        self.flash[pitch] = time.monotonic() + max(0.1, float(seconds))
        self._last_key = None
        self.update()

    def has_flash(self) -> bool:
        now = time.monotonic()
        for p in list(self.flash):
            if self.flash[p] <= now:
                del self.flash[p]
        return bool(self.flash)

    def clear_flash(self):
        self.flash.clear()
        self.update()

    def _hint(self, p: QPainter, text: str):
        p.setPen(QPen(T.TEXT_DIM))
        f = QFont()
        f.setPointSizeF(15)
        p.setFont(f)
        p.drawText(QRectF(0, 0, self.width(), self.height()),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _current_group(self) -> list[tuple[list[tuple[int, int]], bool, str]]:
        """往后取 preview_count 个音符，转成 [(格子列表, 是否休止, 音名)]。"""
        if not self.timeline or not self.timeline.items:
            return []
        out = []
        for item in self.timeline.upcoming(self.sec, self.preview_count):
            cells = []
            for pitch in item.chord.pitches:
                cell = layout.pitch_to_cell(pitch)
                if cell is not None:
                    cells.append(cell)
            out.append((cells, item.chord.is_rest,
                        '+'.join(item.chord.pitches)))
        return out


class GridView(SheetView):
    """4×4 网格高亮式。"""

    HEADER_H = 50
    FOOTER_H = 40
    PAD = 12

    def set_time(self, sec: float):
        """这一帧画的东西没变就不重绘。

        时钟跑到 ~120fps，但网格只在「当前音换人了」那一刻才真的变，
        所以这里做个指纹比对：绝大多数 tick 是零开销的。
        """
        self.sec = sec
        key = tuple((tuple(cells), rest, name)
                    for cells, rest, name in self._current_group())
        if key != self._last_key:
            self._last_key = key
            self.update()

    def paintEvent(self, _ev):
        if self.hidden:
            return                       # 不画 = 全透明（窗口还在，鼠标照样能抓）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._panel(p)

        if not self.timeline or not self.timeline.items:
            self._hint(p, '还没有谱子')
            return

        w, h = self.width(), self.height()
        group = self._current_group()
        if not group:
            self._hint(p, '演奏结束')
            return

        cur_cells, cur_rest, cur_name = group[0]

        # ---- 顶部：当前该打的音 ----
        label = ('休止 %s 拍' % _fmt(self.timeline.item_at(self.sec).chord.duration)
                 if cur_rest else (cur_name or '—'))
        p.setPen(QPen(T.ACTIVE if not cur_rest else T.TEXT_DIM))
        f = QFont()
        f.setPointSizeF(min(30.0, h * 0.075))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(self.PAD, 4, w - 2 * self.PAD, self.HEADER_H - 6),
                   Qt.AlignmentFlag.AlignCenter, label)

        # ---- 中间：4×4 网格 ----
        top = self.HEADER_H
        avail_w = w - 2 * self.PAD
        avail_h = h - self.HEADER_H - self.FOOTER_H
        side = min(avail_w, avail_h)
        cell = (side - 3 * T.GAP) / 4.0
        ox = (w - side) / 2.0
        oy = top + (avail_h - side) / 2.0

        # 每个格子 -> 它在预览里的序号（0 = 当前）
        order: dict[tuple[int, int], int] = {}
        for rank, (cells, _rest, _name) in enumerate(group):
            for cs in cells:
                order.setdefault(cs, rank)

        for row in range(4):
            for col in range(4):
                # 行 3 画在最上面（跟游戏画面一致）
                y = oy + (3 - row) * (cell + T.GAP)
                x = ox + col * (cell + T.GAP)
                rect = QRectF(x, y, cell, cell)

                rank = order.get((row, col))
                if rank is None:
                    fill, edge, txt = T.CELL, T.CELL_EDGE, T.TEXT_DIM
                    inset = 0.0
                elif rank == 0:
                    fill, edge, txt = T.ACTIVE, T.ACTIVE_EDGE, T.ACTIVE_TEXT
                    inset = -cell * 0.05          # 当前格微微放大
                else:
                    idx = min(rank - 1, len(T.UPCOMING) - 1)
                    fill = T.UPCOMING[idx]
                    edge = T.UPCOMING[idx].lighter(125)
                    txt = T.ACTIVE_TEXT
                    inset = 0.0

                r = rect.adjusted(inset, inset, -inset, -inset)
                p.setPen(QPen(self._dim(edge), 3 if rank == 0 else 1.5))
                p.setBrush(QBrush(self._dim(fill)))
                p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

                if self.show_labels:
                    p.setPen(QPen(txt))
                    p.setFont(_fit_font(cell * 0.34, bold=(rank == 0)))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                               layout.cell_to_pitch(row, col))

        # ---- 实时跟弹：刚听到的键，盖一层亮青 ----
        now = time.monotonic()
        for fp in list(self.flash):
            left = self.flash[fp] - now
            if left <= 0:
                del self.flash[fp]
                continue
            pcell = layout.pitch_to_cell(fp)
            if pcell is None:
                continue
            prow, pcol = pcell
            k = min(1.0, left / 0.35)
            fx = ox + pcol * (cell + T.GAP)
            fy = oy + (3 - prow) * (cell + T.GAP)
            fr = QRectF(fx, fy, cell, cell).adjusted(
                -cell * 0.03, -cell * 0.03, cell * 0.03, cell * 0.03)
            p.setPen(QPen(QColor(130, 255, 225, int(200 * k + 45)), 4.0))
            p.setBrush(QBrush(QColor(90, 240, 200, int(140 * k + 25))))
            p.drawRoundedRect(fr, T.RADIUS, T.RADIUS)

        # ---- 底部：后面几个音的名字 ----
        p.setFont(_fit_font(max(9.0, h * 0.028)))
        names = []
        for rank, (cells, rest, name) in enumerate(group[1:], start=1):
            names.append(('休止' if rest else name) if cells or rest else '?')
        tail = ' → '.join(names[:4]) if names else ''
        p.setPen(QPen(T.TEXT_DIM))
        p.drawText(QRectF(self.PAD, h - self.FOOTER_H, w - 2 * self.PAD,
                          self.FOOTER_H - 6),
                   Qt.AlignmentFlag.AlignCenter,
                   ('下一个：' + tail) if tail else '')

        # ---- 提示：谱子里有琴弹不出来的音 ----
        bad = _unmapped(self.timeline)
        if bad:
            p.setPen(QPen(QColor(255, 130, 130)))
            p.setFont(_fit_font(max(9.0, h * 0.026)))
            p.drawText(QRectF(self.PAD, h - self.FOOTER_H + 14,
                              w - 2 * self.PAD, 20),
                       Qt.AlignmentFlag.AlignCenter,
                       '琴上没有这些音，会被跳过：' + ' '.join(bad[:6]))


# ---------------- 小工具 ----------------

def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return '%g' % x


def _fit_font(size: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(max(7.0, size))
    f.setBold(bold)
    return f


def _unmapped(tl: Timeline) -> list[str]:
    return layout.unmapped_pitches(tl.all_pitches())
