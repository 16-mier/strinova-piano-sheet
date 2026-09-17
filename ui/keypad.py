# -*- coding: utf-8 -*-
"""打击垫 —— 点了就出声，可以边弹边写谱。

跟游戏里那台琴一样的 4×4 排列，**只认鼠标**。

★ 故意不绑电脑键盘 ★
   以前绑过 1234/qwer/asdf/zxcv，结果在别处打字、或者在游戏里按技能键，
   都会莫名其妙触发打击垫、把音符写进谱子里。现在彻底解绑，
   焦点策略也设成 NoFocus —— 点它不会把文本框的焦点抢走。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget
from PyQt6.QtMultimedia import QSoundEffect

from core import layout, synth
from core.paths import app_dir

from . import theme as T

# 每个音准备几个播放实例轮换，这样同一个音快速连按不会互相打断
_POOL = 3


class NotePlayer:
    """用 QSoundEffect 播音符（低延迟，能同时响多个音）。"""

    def __init__(self, volume: float = 0.75):
        self.volume = volume
        self._pool: dict[str, list[QSoundEffect]] = {}
        self._next: dict[str, int] = {}
        self.ready = False

    def load_all(self) -> int:
        """生成（首次）并载入 16 个音，返回载入数量。"""
        notes_dir = os.path.join(app_dir(), 'assets', 'notes')
        try:
            files = synth.ensure_notes(notes_dir)
        except Exception:
            return 0

        count = 0
        for pitch, path in files.items():
            try:
                pool = []
                for _ in range(_POOL):
                    e = QSoundEffect()
                    e.setSource(QUrl.fromLocalFile(path))
                    e.setVolume(self.volume)
                    pool.append(e)
                self._pool[pitch] = pool
                self._next[pitch] = 0
                count += 1
            except Exception:
                continue
        self.ready = count > 0
        return count

    def play(self, pitch: str):
        pool = self._pool.get(pitch)
        if not pool:
            return
        i = self._next.get(pitch, 0) % len(pool)
        self._next[pitch] = i + 1
        try:
            pool[i].play()
        except Exception:
            pass

    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, v))
        for pool in self._pool.values():
            for e in pool:
                e.setVolume(self.volume)


class KeyPad(QWidget):
    """4×4 打击垫。点一下 = 出一声 + 发 note_clicked 信号。"""

    note_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = NotePlayer()
        self._flash: dict[tuple[int, int], int] = {}     # 格子 -> 剩余闪烁帧
        self._pressed: set[tuple[int, int]] = set()
        self.setMinimumSize(260, 260)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 只认鼠标，不抢焦点
        self.setMouseTracking(False)

    # ---- 音源 ----

    def load_notes(self) -> int:
        n = self.player.load_all()
        self.update()
        return n

    # ---- 坐标换算 ----

    def _cell_rect(self, row: int, col: int) -> QRectF:
        gap = 6.0
        side = min(self.width(), self.height())
        cell = (side - 3 * gap) / 4.0
        ox = (self.width() - side) / 2.0
        oy = (self.height() - side) / 2.0
        # 行 3 画在最上面
        return QRectF(ox + col * (cell + gap),
                      oy + (3 - row) * (cell + gap),
                      cell, cell)

    def _hit(self, pos) -> tuple[int, int] | None:
        for row in range(4):
            for col in range(4):
                if self._cell_rect(row, col).contains(float(pos.x()),
                                                      float(pos.y())):
                    return (row, col)
        return None

    # ---- 触发 ----

    def trigger(self, row: int, col: int):
        pitch = layout.cell_to_pitch(row, col)
        self.player.play(pitch)
        self._flash[(row, col)] = 8
        self.update()
        self.note_clicked.emit(pitch)

    def flash(self, pitch: str):
        """外部（试听播放器）触发的闪烁，不出声。"""
        cell = layout.pitch_to_cell(pitch)
        if cell is None:
            return
        self._flash[cell] = 8
        self.update()

    # ---- 鼠标 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit(event.position())
            if hit:
                self._pressed.add(hit)
                self.trigger(*hit)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed.clear()
        self.update()
        super().mouseReleaseEvent(event)

    # ---- 闪烁 ----

    def tick(self):
        """由外部定时器调用，让按下的格子闪一下。"""
        if not self._flash:
            return
        dead = [k for k, v in self._flash.items() if v <= 1]
        for k in dead:
            del self._flash[k]
        for k in self._flash:
            self._flash[k] -= 1
        self.update()

    # ---- 绘制 ----

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.player.ready:
            p.setPen(QPen(T.TEXT_DIM))
            f = QFont()
            f.setPointSizeF(11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '音源还没准备好')
            return

        for row in range(4):
            for col in range(4):
                r = self._cell_rect(row, col)
                pitch = layout.cell_to_pitch(row, col)
                flash = self._flash.get((row, col), 0)

                if flash > 0:
                    k = flash / 8.0
                    fill = QColor(255, 206, 48, int(120 + 135 * k))
                    txt = T.ACTIVE_TEXT
                else:
                    fill = T.CELL
                    txt = T.TEXT

                p.setPen(QPen(T.CELL_EDGE, 1.6))
                p.setBrush(QBrush(fill))
                p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

                p.setPen(QPen(txt))
                f = QFont()
                f.setPointSizeF(max(9.0, r.width() * 0.24))
                f.setBold(flash > 0)
                p.setFont(f)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, pitch)
