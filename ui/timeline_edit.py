# -*- coding: utf-8 -*-
"""时间轴编辑器 —— 钢琴卷帘 + 区域选择。

横向 = 拍数，纵向 = **16 行，每个键一行**（跟游戏里一样：`1''` 在最上、`1` 在最下）。

操作一览
    拖音符            改它跟前一个音之间的间距（自动插/删休止符）
    点音符            选中 + 试听
    双击音符          从它这里开始播
    在空白处拖        框选一段区域
    点空白            把播放头挪过去
    Ctrl + 滚轮       缩放
    左右方向键        微调选中音符的间距（1/4 拍）
    Delete            有选区就删选区，否则删选中的音
    Ctrl + A          全选
    Esc               取消选区
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core import layout
from core.edit_model import EditModel, EdNote

from . import theme as T

ROW_H = 24          # 每个键一行的高度
HEADER_W = 58       # 左边音名列宽
SNAP = 0.25         # 拖动吸附精度（拍）
TOTAL_ROWS = 16     # 4×4

SEL_FILL = QColor(86, 168, 255, 58)
SEL_EDGE = QColor(130, 195, 255, 190)


class TimelineEditor(QWidget):
    """钢琴卷帘编辑器（支持框选）。"""

    note_clicked = pyqtSignal(str)      # 点了某个音（用于试听）
    changed = pyqtSignal()              # 模型被改过
    playhead_moved = pyqtSignal(float)  # 播放头挪到第几拍
    seek_requested = pyqtSignal(float)  # 请求从某拍开始播
    selection_changed = pyqtSignal()    # 选区变了

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: EditModel | None = None
        self.px_per_beat = 64.0
        self.selected: EdNote | None = None
        self.playhead = 0.0

        # 选区（拍）
        self.sel_start: float | None = None
        self.sel_end: float | None = None
        self._band_anchor: float | None = None
        self._band_moved = False

        # 拖动音符
        self._dragging = False
        self._drag_x0 = 0.0
        self._drag_gap0 = 0.0
        self._drag_moved = False

        self.setMinimumHeight(TOTAL_ROWS * ROW_H + 24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------- 数据 ----------------

    def set_model(self, model: EditModel | None):
        self.model = model
        self.selected = None
        self.playhead = 0.0
        self.clear_selection()
        self._update_size()
        self.update()

    def set_model_keep_head(self, model: EditModel | None, playhead: float):
        """换模型但**保留播放头**（打谱时用，不然每敲一下都被踢回开头）。"""
        self.model = model
        self.selected = None
        self.set_playhead(playhead)
        self._update_size()
        self.update()

    def set_playhead(self, beat: float):
        if self.model:
            beat = max(0.0, min(beat, self.model.total_beats))
        self.playhead = beat
        self.update()

    def _update_size(self):
        if not self.model:
            return
        w = HEADER_W + int(self.model.total_beats * self.px_per_beat) + 60
        self.setMinimumWidth(max(400, w))

    # ---------------- 选区 ----------------

    def selection_beats(self) -> tuple[float, float] | None:
        """返回 (起, 止) 拍；没有有效选区时返回 None。"""
        if self.sel_start is None or self.sel_end is None:
            return None
        if abs(self.sel_end - self.sel_start) < 1e-6:
            return None
        return (min(self.sel_start, self.sel_end),
                max(self.sel_start, self.sel_end))

    def has_selection(self) -> bool:
        return self.selection_beats() is not None

    def select_all(self):
        if self.model and self.model.notes:
            self.sel_start, self.sel_end = 0.0, self.model.total_beats
            self.update()
            self.selection_changed.emit()

    def clear_selection(self):
        self.sel_start = self.sel_end = None
        self._band_anchor = None
        self.update()
        self.selection_changed.emit()

    def set_selection(self, a: float, b: float):
        if self.model:
            a = max(0.0, min(a, self.model.total_beats))
            b = max(0.0, min(b, self.model.total_beats))
        self.sel_start, self.sel_end = a, b
        self.update()
        self.selection_changed.emit()

    def delete_selection(self) -> int:
        """删除选区内的块（后面的内容往前接上）。"""
        rng = self.selection_beats()
        if not rng or not self.model:
            return 0
        n = self.model.remove_range(*rng)
        self.clear_selection()
        self.selected = None
        self._update_size()
        self.update()
        self.changed.emit()
        return n

    # ---------------- 坐标 ----------------

    def _beat_x(self, beat: float) -> float:
        return HEADER_W + beat * self.px_per_beat

    def _x_beat(self, x: float) -> float:
        return (x - HEADER_W) / max(1e-6, self.px_per_beat)

    def _cell_y(self, row: int, col: int) -> float:
        """格子 -> y。PAD 序号越大越靠上（PAD16 在最顶）。"""
        return (TOTAL_ROWS - 1 - (row * layout.PAD_COLS + col)) * ROW_H

    def _pitch_y(self, pitch: str) -> float | None:
        cell = layout.pitch_to_cell(pitch)
        return None if cell is None else self._cell_y(cell[0], cell[1])

    def _note_rect(self, note: EdNote, pitch: str) -> QRectF | None:
        y = self._pitch_y(pitch)
        if y is None:
            return None
        x1 = self._beat_x(note.start)
        x2 = self._beat_x(note.end)
        return QRectF(x1, y + 2, max(7.0, x2 - x1 - 1), ROW_H - 4)

    def _hit(self, pos) -> EdNote | None:
        if not self.model or pos.x() < HEADER_W:
            return None
        beat = self._x_beat(pos.x())
        for n in reversed(self.model.notes):
            if n.is_rest:
                continue
            if not (n.start - 0.001 <= beat <= n.end + 0.001):
                continue
            for pitch in n.pitches:
                y = self._pitch_y(pitch)
                if y is not None and y <= pos.y() <= y + ROW_H:
                    return n
        return None

    # ---------------- 交互 ----------------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.model:
            super().mousePressEvent(event)
            return
        pos = event.position()
        hit = self._hit(pos)

        if hit is not None:
            # --- 拖音符 ---
            self.selected = hit
            self._dragging = True
            self._drag_moved = False
            self._drag_x0 = pos.x()
            self._drag_gap0 = self.model.gap_of(hit)
            self.setFocus()
            for pitch in hit.pitches:
                self.note_clicked.emit(pitch)
            self.update()
        else:
            # --- 可能是框选，也可能只是点一下 ---
            beat = max(0.0, self._x_beat(pos.x()))
            self._band_anchor = beat
            self._band_moved = False
            self._dragging = False
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.clear_selection()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.model:
            super().mouseMoveEvent(event)
            return
        pos = event.position()

        # --- 框选 ---
        if self._band_anchor is not None:
            cur = max(0.0, self._x_beat(pos.x()))
            if abs(cur - self._band_anchor) * self.px_per_beat > 4:
                self._band_moved = True
            if self._band_moved:
                self.sel_start, self.sel_end = self._band_anchor, cur
                self.update()
                self.selection_changed.emit()
            event.accept()
            return

        # --- 拖音符 ---
        if self._dragging and self.selected:
            dx = pos.x() - self._drag_x0
            if abs(dx) > 3:
                self._drag_moved = True
            want = self._drag_gap0 + dx / max(1e-6, self.px_per_beat)
            want = max(0.0, round(want / SNAP) * SNAP)
            if self.model.set_gap_of(self.selected, want):
                self._update_size()
                self.update()
                self.changed.emit()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # --- 结束框选 ---
        if self._band_anchor is not None:
            was_moved = self._band_moved
            anchor = self._band_anchor
            self._band_anchor = None
            self._band_moved = False
            if not was_moved:
                # 没拖动 = 只是点了一下空白：移动播放头
                self.set_playhead(max(0.0, anchor))
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return

        # --- 结束拖音符 ---
        if self._dragging:
            self._dragging = False
            if not self._drag_moved and self.model and self.selected:
                self.set_playhead(self.selected.start)
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击音符 = 从它开始播。"""
        hit = self._hit(event.position())
        if hit is not None:
            self.set_playhead(hit.start)
            self.seek_requested.emit(hit.start)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        """Ctrl + 滚轮 = 缩放时间轴。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.px_per_beat = max(12.0, min(400.0,
                                             self.px_per_beat * factor))
            self._update_size()
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.clear_selection()
            self.selected = None
            self.update()
            event.accept()
            return

        if key == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            self.select_all()
            event.accept()
            return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.has_selection():
                self.delete_selection()
            elif self.model and self.selected is not None:
                self.model.remove_note(self.model.index_of(self.selected))
                self.selected = None
                self._update_size()
                self.update()
                self.changed.emit()
            event.accept()
            return

        if self.model and self.selected is not None:
            if key == Qt.Key.Key_Left:
                self.model.set_gap_of(
                    self.selected,
                    max(0.0, self.model.gap_of(self.selected) - SNAP))
            elif key == Qt.Key.Key_Right:
                self.model.set_gap_of(
                    self.selected,
                    self.model.gap_of(self.selected) + SNAP)
            else:
                super().keyPressEvent(event)
                return
            self._update_size()
            self.update()
            self.changed.emit()
            event.accept()
            return

        super().keyPressEvent(event)

    # ---------------- 绘制 ----------------

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(0, 0, w, h, T.BG)

        if not self.model or not self.model.notes:
            p.setPen(QPen(T.TEXT_DIM))
            f = QFont()
            f.setPointSizeF(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '左边写点音符，这里就会出现时间轴')
            return

        total = max(1.0, self.model.total_beats)

        # ---- 16 行底纹 ----
        p.setPen(Qt.PenStyle.NoPen)
        for row in range(layout.PAD_ROWS):
            for col in range(layout.PAD_COLS):
                y = self._cell_y(row, col)
                zone = T.ZONE_COLORS[T.zone_of(row)]
                c = QColor(zone)
                c.setAlpha(30 if (row + col) % 2 else 14)
                p.setBrush(QBrush(c))
                p.drawRect(QRectF(HEADER_W, y, w - HEADER_W, ROW_H))

        # ---- 选区高亮（画在网格下面、音符上面之前更有层次）----
        rng = self.selection_beats()
        if rng:
            x1 = self._beat_x(rng[0])
            x2 = self._beat_x(rng[1])
            p.setPen(QPen(SEL_EDGE, 1.2))
            p.setBrush(QBrush(SEL_FILL))
            p.drawRect(QRectF(x1, 0, max(1.0, x2 - x1), h))

        # ---- 拍网格 ----
        f = QFont()
        f.setPointSizeF(8.5)
        p.setFont(f)
        beat = 0.0
        while beat <= total + 1e-9:
            x = self._beat_x(beat)
            if x > HEADER_W - 1:
                is_beat = abs(beat - round(beat)) < 1e-9
                is_bar = is_beat and round(beat) % 4 == 0
                if is_bar:
                    p.setPen(QPen(QColor(255, 255, 255, 72), 1.4))
                elif is_beat:
                    p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
                else:
                    p.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
                p.drawLine(int(x), 0, int(x), h)
                if is_bar:
                    p.setPen(QPen(T.TEXT_DIM))
                    p.drawText(QRectF(x + 3, h - 15, 44, 13),
                               Qt.AlignmentFlag.AlignLeft, '%d' % round(beat))
            beat += 0.25

        # ---- 休止符占位 ----
        for n in self.model.notes:
            if not n.is_rest:
                continue
            x1 = self._beat_x(n.start)
            x2 = self._beat_x(n.end)
            if x2 - x1 < 3:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 16)))
            p.drawRect(QRectF(x1, 1, max(2.0, x2 - x1 - 1), h - 16))

        # ---- 音符 ----
        for n in self.model.notes:
            if n.is_rest:
                continue
            sel = (n is self.selected)
            in_sel = bool(rng and n.end > rng[0] + 1e-9
                          and n.start < rng[1] - 1e-9)
            for pitch in n.pitches:
                r = self._note_rect(n, pitch)
                if r is None:
                    continue
                cell = layout.pitch_to_cell(pitch)
                base = (T.ZONE_COLORS[T.zone_of(cell[0])]
                        if cell else T.TEXT)
                if sel:
                    p.setPen(QPen(QColor(255, 255, 255), 2.2))
                    p.setBrush(QBrush(T.ACTIVE))
                else:
                    edge = QColor(255, 255, 255, 230) if in_sel \
                        else base.lighter(125)
                    p.setPen(QPen(edge, 2.0 if in_sel else 1.2))
                    p.setBrush(QBrush(base))
                p.drawRoundedRect(r, 5, 5)

                if r.width() > 24:
                    f2 = QFont()
                    f2.setPointSizeF(8.5)
                    f2.setBold(sel)
                    p.setFont(f2)
                    p.setPen(QPen(T.ACTIVE_TEXT if sel
                                  else QColor(18, 22, 30)))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, pitch)

        # ---- 播放头 ----
        px = self._beat_x(self.playhead)
        if px >= HEADER_W:
            p.setPen(QPen(QColor(255, 96, 96), 2.0))
            p.drawLine(int(px), 0, int(px), h)

        # ---- 左侧音名列 ----
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(16, 19, 27, 240)))
        p.drawRect(QRectF(0, 0, HEADER_W, h))
        p.setPen(QPen(T.BG_EDGE, 1))
        p.drawLine(HEADER_W, 0, HEADER_W, h)

        f3 = QFont()
        f3.setPointSizeF(9.0)
        p.setFont(f3)
        for row in range(layout.PAD_ROWS):
            for col in range(layout.PAD_COLS):
                y = self._cell_y(row, col)
                pitch = layout.cell_to_pitch(row, col)
                zone = T.ZONE_COLORS[T.zone_of(row)]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(zone))
                p.drawRoundedRect(QRectF(4, y + ROW_H / 2 - 3, 6, 6), 2, 2)
                p.setPen(QPen(T.TEXT))
                p.drawText(QRectF(14, y, HEADER_W - 20, ROW_H),
                           Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter, pitch)
