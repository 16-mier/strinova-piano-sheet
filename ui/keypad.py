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
    """4×4 打击垫。点一下 = 出一声 + 发 note_clicked 信号。

    ★ 三种多选方式 ★
      · 按住左键拖着划过去 —— 划过的格子都进选区，松手一起落笔
      · Shift + 拖动 —— 矩形框选一整"段"，松手一起落笔
      · Shift + 单击 —— **逐个挑**：点一下进绿色名单，**再点同一个就取消**
        这个是累加的，隔得老远的几个键也能慢慢挑，不像拖动那样容易划错。
        挑完按「⬒ 写和弦」落笔；右键点一下垫子 = 把挑好的清空。

    ★ 为什么「松手才写入」★
      按下只**出声预览**，松手才真正落笔 —— 按下去就写的话，
      手一抖点歪了也得再按一次撤销。单击的手感没变（按下出声、
      松手落笔，中间差不到一瞬）。

    ★ 那套"多选写和弦"已经删了 ★ —— 用户：「和弦功能用不到」

      原来这里有三种**一起落笔**的方式：按住拖动划过一串、
      Shift 拖出一个矩形、Shift 逐个挑再按「写和弦」按钮。
      它们服务的都是同一件事：同一时刻按下好几个键 = 谱面里一个
      `1&3&5` 的和弦块。

      用户不写和弦，整套就没用了，只留最直接的"点一下写一个音"。
      （谱面里本来就有的和弦照样能显示、能编辑 —— 那是
        `parser` / `EditModel` 的事，跟这里没关系。）
    """

    note_clicked = pyqtSignal(str)      # 点了一个键（松手时才发）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = NotePlayer()
        self._flash: dict[tuple[int, int], int] = {}      # 格子 -> 剩余闪烁帧
        self._press_cell: tuple[int, int] | None = None   # 按下时命中的那一格
        self.setMinimumSize(260, 260)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 只认鼠标，不抢焦点
        self.setMouseTracking(False)
        self.setToolTip('点一下出声，松手写进谱面。\n'
                        '（会不会真写进去，看上边那个「同步写入谱面」）')

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

    def strike(self, row: int, col: int):
        """按一下的手感：出声 + 闪一下（**不发信号**，写入由松手时决定）。"""
        self.player.play(layout.cell_to_pitch(row, col))
        self._flash[(row, col)] = 8
        self.update()

    def trigger(self, row: int, col: int):
        """完整触发：出声 + 闪 + 发 note_clicked。

        ★ 只有测试在用 ★（`tools/test_editor_chord.py`）——
          生产代码走鼠标那条路：按下 `strike()`、松手才发信号。
        """
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
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hit = self._hit(event.position())
        if not hit:
            super().mousePressEvent(event)
            return
        self._press_cell = hit
        self.strike(*hit)               # 按下先出声，让人知道点着了
        event.accept()

    def mouseReleaseEvent(self, event):
        """松手落笔 —— 点一下写一个音。

        ★ 只剩这一条路了 ★
          以前这里还分"一格发单音 / 多格发和弦"，那套多选已经删了
          （用户：「和弦功能用不到」）。

        ★ 还得确认"松手的时候手还在同一格"★
          按下之后划出去再松开（本来想点 A、结果拖到了 B 上），
          不该把 A 写进去 —— 那属于"点空了"，什么都不写。
        """
        cell = self._press_cell
        self._press_cell = None
        if (event.button() == Qt.MouseButton.LeftButton
                and cell is not None):
            if self._hit(event.position()) == cell:
                self.note_clicked.emit(layout.cell_to_pitch(*cell))
            event.accept()
            return
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
                # （原来这里还有两种底色：拖动中的蓝色、Shift 挑中的
                #   绿色 —— 那套多选删了，现在只剩"按下去黄色一闪"。）
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
