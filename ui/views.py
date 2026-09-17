# -*- coding: utf-8 -*-
"""两种谱面显示。

GridView  4×4 网格高亮式 —— 跟游戏里那台琴的排列一模一样，当前该打的键亮黄
FallView  下落式 —— 16 条轨道，音符从上往下掉，落到底部判定线就是该打的时候
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core import layout
from core.timeline import Timeline

from . import theme as T


class SheetView(QWidget):
    """两种视图的公共部分。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.sec = 0.0
        self.preview_count = 5
        self.show_labels = True
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    # ---- 数据接口 ----

    def set_timeline(self, tl: Timeline | None):
        self.timeline = tl
        self.update()

    def set_time(self, sec: float):
        self.sec = sec
        self.update()

    # ---- 工具 ----

    def _panel(self, p: QPainter):
        w, h = self.width(), self.height()
        p.setPen(QPen(T.BG_EDGE, 2))
        p.setBrush(QBrush(T.BG))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 16, 16)

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

    def paintEvent(self, _ev):
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
                p.setPen(QPen(edge, 3 if rank == 0 else 1.5))
                p.setBrush(QBrush(fill))
                p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

                if self.show_labels:
                    p.setPen(QPen(txt))
                    p.setFont(_fit_font(cell * 0.34, bold=(rank == 0)))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                               layout.cell_to_pitch(row, col))

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


class FallView(SheetView):
    """下落式 —— 16 条轨道，音符从上往下掉。"""

    HEADER_H = 26
    JUDGE_H = 54
    LOOKAHEAD = 3.2          # 屏幕上显示未来多少秒

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._panel(p)

        if not self.timeline or not self.timeline.items:
            self._hint(p, '还没有谱子')
            return

        w, h = self.width(), self.height()
        pad = 10
        lane_w = (w - 2 * pad) / 16.0
        top = self.HEADER_H
        judge_y = h - self.JUDGE_H
        span = judge_y - top
        if span <= 0 or lane_w <= 0:
            return

        px_per_sec = span / self.LOOKAHEAD

        # ---- 轨道底纹 + 列头音名 ----
        font = _fit_font(min(11.0, lane_w * 0.52))
        p.setFont(font)
        for i in range(16):
            row, col = divmod(i, 4)
            x = pad + i * lane_w
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 8 if i % 2 else 18)))
            p.drawRect(QRectF(x + 1, top, lane_w - 2, span))
            p.setPen(QPen(T.TEXT_DIM))
            p.drawText(QRectF(x, 2, lane_w, self.HEADER_H - 4),
                       Qt.AlignmentFlag.AlignCenter,
                       layout.cell_to_pitch(row, col))

        # ---- 判定线 ----
        p.setPen(QPen(T.ACTIVE, 2.5))
        p.drawLine(pad, judge_y, w - pad, judge_y)

        # ---- 音符 ----
        for item in self.timeline.items:
            y_start = judge_y - (item.start_sec - self.sec) * px_per_sec
            y_end = judge_y - (item.end_sec - self.sec) * px_per_sec
            if y_end < top or y_start > judge_y + 8:
                continue
            active = item.start_sec <= self.sec < item.end_sec
            if item.chord.is_rest:
                continue
            for pitch in item.chord.pitches:
                cell = layout.pitch_to_cell(pitch)
                if cell is None:
                    continue
                i = layout.pad_number(cell[0], cell[1]) - 1
                x = pad + i * lane_w
                y1 = max(y_start, top - 40)
                y2 = min(y_end, judge_y + 6)
                bh = max(6.0, y2 - y1)
                base = T.ZONE_COLORS[T.zone_of(cell[0])]
                if active:
                    p.setPen(QPen(QColor(255, 255, 255), 2.5))
                    p.setBrush(QBrush(T.ACTIVE))
                else:
                    p.setPen(QPen(base.lighter(115), 1.2))
                    p.setBrush(QBrush(base))
                p.drawRoundedRect(
                    QRectF(x + 3, y1, lane_w - 6, bh), 5, 5)

        # ---- 正在响的音名（判定线左侧） ----
        cur = self.timeline.item_at(self.sec)
        if cur and not cur.chord.is_rest:
            p.setPen(QPen(T.ACTIVE))
            p.setFont(_fit_font(max(11.0, h * 0.045), bold=True))
            p.drawText(QRectF(0, judge_y + 4, w, self.JUDGE_H - 6),
                       Qt.AlignmentFlag.AlignCenter,
                       '+'.join(cur.chord.pitches))


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
