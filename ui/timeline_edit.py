# -*- coding: utf-8 -*-
"""时间轴编辑器 —— 钢琴卷帘 + 区域选择。

横向 = 拍数，纵向 = **16 行，每个键一行**（跟游戏里一样：`1''` 在最上、`1` 在最下）。

同一竖列上可以叠好几个音 —— 那就是**和弦**（回写成 `1&3&5`），
游戏里一按就是一串同时响，不会像分解和弦那样听着慢半拍。

操作一览
    拖音符            挪它的位置；拖到前一个音头上 = 并成和弦（同时发声）
    拖音符右边缘      拉长 / 缩短它的时值
    Alt + 拖          把某个音从和弦里拆出来单独挪
    点音符            选中 + 试听
    双击音符          从它这里开始播
    右键点音          把那个音从和弦里拿掉（单音块就是整块删掉）
    按住红线拖        挪播放头
    点一下空白        播放头跳过去
    空白处按住拖      框选一段区域（拖到视野边缘会自动滚）
    Ctrl + 滚轮       缩放
    左右方向键        微调选中音符的间距（1/4 拍）
    Delete            有选区就删选区，否则删选中的音
    Ctrl + Z          撤销
    Ctrl + A          全选
    Esc               取消选区
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainter, QPen,
                         QPolygonF)
from PyQt6.QtWidgets import QWidget

from core import layout
from core.edit_model import EditModel, EdNote

from . import theme as T

ROW_H = 24          # 每个键一行的高度
HEADER_W = 58       # 左边音名列宽
SNAP = 0.25         # 拖动吸附精度（拍）
TOTAL_ROWS = 16     # 4×4

HEAD_GRAB = 6       # 红线左右多少像素内算「抓住了红线」
EDGE_GRAB = 6       # 音符右边缘多少像素内算「要拉时值」
BAND_MIN = 4        # 位移超过这么多像素才算框选（不然就是「点一下挪播放头」）
SCROLL_STEP = 22    # 拖到视野边缘时每次自动滚多少像素
EDGE_ZONE = 26      # 离视野边缘这么近就开始自动滚

SEL_FILL = QColor(86, 168, 255, 58)
SEL_EDGE = QColor(130, 195, 255, 190)
HEAD_COLOR = QColor(255, 96, 96)


class TimelineEditor(QWidget):
    """钢琴卷帘编辑器（支持框选）。"""

    note_clicked = pyqtSignal(str)      # 点了某个音（用于试听）
    changed = pyqtSignal()              # 模型被改过
    playhead_moved = pyqtSignal(float)  # 播放头挪到第几拍
    playhead_dropped = pyqtSignal(float)  # 拖红线松手（正在播就从新位置接着播）
    seek_requested = pyqtSignal(float)  # 请求从某拍开始播
    selection_changed = pyqtSignal()    # 选区变了
    undo_requested = pyqtSignal()       # 改谱之前先让外面存一个撤销点

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: EditModel | None = None
        self.px_per_beat = 64.0
        self.selected: EdNote | None = None
        self.selected_pitch: str | None = None
        self.playhead = 0.0

        # 选区（拍）
        self.sel_start: float | None = None
        self.sel_end: float | None = None
        self._band_anchor: float | None = None
        self._band_moved = False

        # 鼠标正在干什么：None / 'head' 拖红线 / 'note' 挪音符
        #                / 'stretch' 拉时值 / 'band' 框选
        self._mode: str | None = None
        self._drag_x0 = 0.0
        self._drag_start0 = 0.0
        self._dur0 = 0.0
        self._drag_moved = False
        self._sa = None              # 外层 QScrollArea（惰性缓存）
        self._sa_looked = False

        self.setMouseTracking(True)
        self.setMinimumHeight(TOTAL_ROWS * ROW_H + 24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------- 数据 ----------------

    def set_model(self, model: EditModel | None):
        self.model = model
        self.selected = None
        self.selected_pitch = None
        self.playhead = 0.0
        self.clear_selection()
        self._update_size()
        self.update()

    def set_model_keep_head(self, model: EditModel | None, playhead: float):
        """换模型但**保留播放头**（打谱时用，不然每敲一下都被踢回开头）。"""
        self.model = model
        self.selected = None
        self.selected_pitch = None
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
        self.undo_requested.emit()
        n = self.model.remove_range(*rng)
        self.clear_selection()
        self.selected = None
        self.selected_pitch = None
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

    def _hit(self, pos) -> tuple[EdNote, str] | None:
        """点到了哪个块的哪个音 —— 返回 (块, 音名)，没点到返回 None。"""
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
                    return n, pitch
        return None

    # ---------------- 交互 ----------------

    def mousePressEvent(self, event):
        if not self.model:
            super().mousePressEvent(event)
            return
        pos = event.position()
        btn = event.button()

        # --- 右键：把和弦里的这个音拿掉（单音块就是整块删）---
        if btn == Qt.MouseButton.RightButton:
            hit = self._hit(pos)
            if hit is not None:
                note, pitch = hit
                self.undo_requested.emit()
                self.model.remove_pitch(note, pitch)
                self.selected = None
                self.selected_pitch = None
                self._after_edit()
            event.accept()
            return

        if btn != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        self._drag_moved = False

        # --- 红线：按住就能拖着走 ---
        if (pos.x() >= HEADER_W
                and abs(pos.x() - self._beat_x(self.playhead)) <= HEAD_GRAB):
            self._mode = 'head'
            self._band_anchor = None
            self._drag_x0 = pos.x()
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
            return

        hit = self._hit(pos)
        if hit is not None:
            note, pitch = hit
            # Alt + 拖 = 先把这个音从和弦里拆出来，再单独挪它
            if (event.modifiers() & Qt.KeyboardModifier.AltModifier
                    and len(note.pitches) > 1):
                self.undo_requested.emit()
                out = self.model.split_pitch_out(note, pitch)
                if out is not None:
                    note = out
            self.selected = note
            self.selected_pitch = pitch
            self._drag_x0 = pos.x()
            self._drag_start0 = note.start
            self._dur0 = note.dur
            self._mode = ('stretch' if self._near_right_edge(pos, note)
                          else 'note')
            self.setFocus()
            for p in note.pitches:
                self.note_clicked.emit(p)
            self.update()
            event.accept()
            return

        # --- 空白：先当作「点一下挪播放头」，拖出距离才升级成框选 ---
        beat = max(0.0, self._x_beat(pos.x()))
        self._band_anchor = beat
        self._band_moved = False
        self._mode = 'band'
        if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.clear_selection()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.model:
            super().mouseMoveEvent(event)
            return
        pos = event.position()

        # --- 拖红线 ---
        if self._mode == 'head':
            if abs(pos.x() - self._drag_x0) > 3:
                self._drag_moved = True
            self.set_playhead(max(0.0, self._x_beat(pos.x())))
            self.playhead_moved.emit(self.playhead)
            self._auto_scroll(pos)
            event.accept()
            return

        # --- 框选 ---
        if self._mode == 'band':
            cur = max(0.0, self._x_beat(pos.x()))
            if abs(cur - (self._band_anchor or 0.0)) * self.px_per_beat > BAND_MIN:
                self._band_moved = True
            if self._band_moved:
                self.sel_start, self.sel_end = self._band_anchor, cur
                self.update()
                self.selection_changed.emit()
            self._auto_scroll(pos)
            event.accept()
            return

        # --- 挪音符 / 拉时值 ---
        if self._mode in ('note', 'stretch') and self.selected is not None:
            dx = pos.x() - self._drag_x0
            if abs(dx) > 3:
                self._drag_moved = True
            ppb = max(1e-6, self.px_per_beat)
            if self._mode == 'stretch':
                want = max(SNAP, round((self._dur0 + dx / ppb) / SNAP) * SNAP)
                if self.model.set_dur_of(self.selected, want):
                    self._after_edit()
            else:
                want = round((self._drag_start0 + dx / ppb) / SNAP) * SNAP
                got = self.model.move_to_beat(self.selected, want)
                if got is None:
                    pass
                elif got is not self.selected:
                    # 并进前一个音变成和弦了 —— 这一拖就到此为止，
                    # 不然接着拖的是「前一个音」，语义会打结。
                    self.selected = got
                    self._mode = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    self._after_edit()
                else:
                    self._after_edit()
            event.accept()
            return

        # --- 没按键：经过红线时换个光标，提示「这里能拖」---
        near = (pos.x() >= HEADER_W
                and abs(pos.x() - self._beat_x(self.playhead)) <= HEAD_GRAB)
        self.setCursor(Qt.CursorShape.SizeHorCursor if near
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        mode = self._mode
        self._mode = None
        if mode is not None:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        # --- 结束框选 ---
        if mode == 'band':
            was_moved = self._band_moved
            anchor = self._band_anchor or 0.0
            self._band_anchor = None
            self._band_moved = False
            if not was_moved:
                # 没拖出距离 = 只是点了一下空白：播放头跳过去
                self.set_playhead(max(0.0, anchor))
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return

        # --- 结束挪音符 / 拉时值 ---
        if mode in ('note', 'stretch'):
            if (not self._drag_moved and self.selected is not None
                    and self.model is not None
                    and self.model.index_of(self.selected) >= 0):
                self.set_playhead(self.selected.start)
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return

        # --- 红线松手：正在播的话就从新位置接着播 ---
        if mode == 'head':
            if self._drag_moved:
                self.playhead_dropped.emit(self.playhead)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    # ---------------- 小工具 ----------------

    def _near_right_edge(self, pos, note: EdNote) -> bool:
        """鼠标是不是压在块的右边缘上（要拉时值）—— 太窄的块不掺和。"""
        x1 = self._beat_x(note.start)
        x2 = self._beat_x(note.end)
        if x2 - x1 < EDGE_GRAB * 2.5:
            return False
        return abs(pos.x() - x2) <= EDGE_GRAB

    def _after_edit(self):
        """改完模型统一收尾。"""
        self._update_size()
        self.update()
        self.changed.emit()

    def _scroll_area(self):
        """外层的 QScrollArea（找一次就记住）。"""
        if not self._sa_looked:
            self._sa_looked = True
            from PyQt6.QtWidgets import QScrollArea
            w = self.parentWidget()
            while w is not None:
                if isinstance(w, QScrollArea):
                    self._sa = w
                    break
                w = w.parentWidget()
        return self._sa

    def _auto_scroll(self, pos):
        """拖到视野边缘就自己滚 —— 长曲子一口气拖到底不用松手。"""
        sa = self._scroll_area()
        if sa is None:
            return
        vis = self.visibleRegion().boundingRect()
        if vis.isEmpty():
            return
        bar = sa.horizontalScrollBar()
        if pos.x() > vis.right() - EDGE_ZONE:
            bar.setValue(bar.value() + SCROLL_STEP)
        elif pos.x() < vis.left() + EDGE_ZONE:
            bar.setValue(max(0, bar.value() - SCROLL_STEP))

    def mouseDoubleClickEvent(self, event):
        """双击音符 = 从它开始播。"""
        hit = self._hit(event.position())
        if hit is not None:
            note, _pitch = hit
            self.set_playhead(note.start)
            self.seek_requested.emit(note.start)
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
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if key == Qt.Key.Key_Escape:
            self.clear_selection()
            self.selected = None
            self.selected_pitch = None
            self.update()
            event.accept()
            return

        if key == Qt.Key.Key_Z and ctrl:
            self.undo_requested.emit()
            event.accept()
            return

        if key == Qt.Key.Key_A and ctrl:
            self.select_all()
            event.accept()
            return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.has_selection():
                self.delete_selection()
            elif self.model and self.selected is not None:
                self.undo_requested.emit()
                self.model.remove_note(self.model.index_of(self.selected))
                self.selected = None
                self.selected_pitch = None
                self._after_edit()
            event.accept()
            return

        if (ctrl and self.model and self.selected is not None
                and key in (Qt.Key.Key_Left, Qt.Key.Key_Right)):
            # Ctrl + 左右 = 拉长 / 缩短时值（跟拖右边缘等效）
            self.undo_requested.emit()
            d = SNAP if key == Qt.Key.Key_Right else -SNAP
            self.model.set_dur_of(self.selected,
                                  max(SNAP, self.selected.dur + d))
            self._after_edit()
            event.accept()
            return

        if self.model and self.selected is not None:
            if key == Qt.Key.Key_Left:
                self.undo_requested.emit()
                self.model.set_gap_of(
                    self.selected,
                    max(0.0, self.model.gap_of(self.selected) - SNAP))
            elif key == Qt.Key.Key_Right:
                self.undo_requested.emit()
                self.model.set_gap_of(
                    self.selected,
                    self.model.gap_of(self.selected) + SNAP)
            else:
                super().keyPressEvent(event)
                return
            self._after_edit()
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

                if len(n.pitches) > 1:
                    # 和弦标记：左边缘一条竖杠，一眼看出这里是「同时按下」
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(QColor(255, 255, 255, 225)))
                    p.drawRoundedRect(
                        QRectF(r.left() + 2, r.top() + 3,
                               2.5, max(2.0, r.height() - 6)), 1.2, 1.2)

                if r.width() > 24:
                    f2 = QFont()
                    f2.setPointSizeF(8.5)
                    f2.setBold(sel)
                    p.setFont(f2)
                    p.setPen(QPen(T.ACTIVE_TEXT if sel
                                  else QColor(18, 22, 30)))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, pitch)

        # ---- 播放头（顶上带个小三角，明示「这条线能按住拖」）----
        px = self._beat_x(self.playhead)
        if px >= HEADER_W:
            p.setPen(QPen(HEAD_COLOR, 2.0))
            p.drawLine(int(px), 0, int(px), h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 96, 96, 215)))
            p.drawPolygon(QPolygonF([QPointF(px - 7, 0), QPointF(px + 7, 0),
                                     QPointF(px, 12)]))

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
