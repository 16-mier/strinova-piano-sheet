# -*- coding: utf-8 -*-
"""编辑器里的试听播放器 —— 按拍走，到点出声。

跟主界面的 Player 不同：这个是**编辑时试听**用的，
可以从任意一拍开始，并且会逐个把音符"弹"出来。
"""

from __future__ import annotations

from PyQt6.QtCore import QElapsedTimer, QObject, Qt, QTimer, pyqtSignal

from core.edit_model import EditModel


class EditPlayer(QObject):
    tick = pyqtSignal(float)          # 当前走到第几拍
    finished = pyqtSignal()
    note_fired = pyqtSignal(str)      # 触发了一个音（让打击垫闪一下）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: EditModel | None = None
        self.bpm = 120
        self.playing = False
        self.beat = 0.0
        self._base = 0.0
        self._clock = QElapsedTimer()
        self._fired: set[int] = set()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._on_tick)

    def set_model(self, model: EditModel | None):
        if self.playing:
            self.stop()
        self.model = model

    def play_from(self, beat: float):
        if not self.model or not self.model.notes:
            return
        self.beat = max(0.0, min(beat, self.model.total_beats))
        self._base = self.beat
        self._fired = set()
        self._clock.restart()
        self.playing = True
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.playing = False

    def toggle_from(self, beat: float):
        if self.playing:
            self.stop()
        else:
            self.play_from(beat)

    def _on_tick(self):
        if not self.model:
            return
        spb = 60.0 / max(1, self.bpm)
        self.beat = self._base + self._clock.elapsed() / 1000.0 / spb

        if self.beat >= self.model.total_beats:
            self.beat = self.model.total_beats
            self.tick.emit(self.beat)
            self.stop()
            self.finished.emit()
            return

        for i, n in enumerate(self.model.notes):
            if n.is_rest or i in self._fired:
                continue
            if n.start <= self.beat:
                self._fired.add(i)
                for pitch in n.pitches:
                    self.note_fired.emit(pitch)

        self.tick.emit(self.beat)
