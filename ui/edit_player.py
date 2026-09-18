# -*- coding: utf-8 -*-
"""编辑器里的试听播放器 —— 按拍走，到点出声。

跟主界面的 Player 不同：这个是**编辑时试听**用的，
可以从任意一拍开始，并且会逐个把音符"弹"出来。

★★ 「拍」在这个项目里是什么 ★★

    ★ 两个字段的单位**不一样** —— 这条以前写错过，而且真的引发了 bug ★

        `Chord.at`     —— **绝对秒**（`core/parser.py` 的契约，
                          也是 `Timeline.start_sec` 的来源）
        `EdNote.start` —— **拍**（`core/edit_model.py`：
                          `start = 秒 / SPB`，字段注释就写着"起始拍"）

    这段注释以前写的是"`EdNote.start` / `Chord.at` 都存**绝对秒**"——
    就是这句错描述让 `timeline_from_notes()` 收到了**拍值**，
    于是制谱器那条路交出去的浮窗时间轴被整个放大一倍
    （demo.txt 在主界面是 28.8 秒，走制谱器变成 55.2 秒），
    而喂进去的播放位置是真实秒 —— 越弹越落后。
    用户报的「都下俩个按键了显示还是上俩个」就是这个。

    换算那一层在 `ui/editor.py` 的 `_SecNote`，那里写了完整的实测数据。

    `TimelineEditor.spb` 是固定的 `SPB = 0.5`（`core/parser.py`），
    它只是"秒 ↔ 拍"的一个换算常数。
    所以在这套代码里，**「拍」就是「秒 × 2」的别名，跟曲子 BPM 无关**。
    曲子标 `#100#` 只是记谱时的历史信息，不影响任何东西落在哪一秒上。

    这个播放器的时钟必须和 `TimelineEditor` 用**同一个** `spb`，
    否则两边对"1 拍是多少秒"的理解不一致，界面就会越走越偏。
    具体踩过的坑见 `_on_tick()` 里那段注释。
"""

from __future__ import annotations

from PyQt6.QtCore import QElapsedTimer, QObject, Qt, QTimer, pyqtSignal

from core.edit_model import SPB, EditModel


class EditPlayer(QObject):
    tick = pyqtSignal(float)          # 当前走到第几拍
    finished = pyqtSignal()
    note_fired = pyqtSignal(str)      # 触发了一个音（让打击垫闪一下）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: EditModel | None = None
        # ★ 这个属性现在**不参与计时** ★
        #   留着只是为了让外部赋值不至于报错（`editor.py` 里有几处
        #   `self.player.bpm = ...`），以及给界面显示用。
        #   真正决定时钟快慢的是 `SPB`，见模块 docstring。
        self.bpm = 120
        self.playing = False
        self.beat = 0.0
        self._base = 0.0
        self._clock = QElapsedTimer()
        self._fired: set[int] = set()
        self._stop_at: float | None = None
        # ★ 区间循环 ★
        #   用户：「选区功能应该是在选区内循环播放」。
        #   `_loop_from` 是折返回到哪儿，`_stop_at` 是走到哪儿折返。
        self._loop = False
        self._loop_from = 0.0
        # ★ 静音：只走时间，**不触发任何音** ★
        #   制谱器的「⏺ 开始记录」用它 —— 时间轴往前走当作时间参考，
        #   用户自己按打击垫，音就落到按下的那一刻。
        #   （如果这时候还照着已有谱子发声，就跟用户弹的混在一起了。）
        self.muted = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._on_tick)

    def set_model(self, model: EditModel | None):
        # ★ 播放中换模型不要停 ★
        #   「边听边写」的时候，每按一个打击垫都会换一次模型 ——
        #   一停就再也连续不起来了（时间轴走一下、写一个音、又停）。
        #   新写的音落在**当前时刻**，`_sort_free()` 会把它排到末尾，
        #   前面那些音的索引不变，所以 `_fired` 记录照样有效。
        self.model = model

    def play_from(self, beat: float, stop_at: float | None = None,
                  keep_fired: bool = False, loop: bool = False):
        """从 beat 开始播；给了 stop_at 就播到那儿自动停（选区试听用）。

        ★ `keep_fired=True` = 别清空"已经响过的音" ★
          看门狗（`EditorDialog._watch_clock`）把卡住的时钟拉起来时传它。
          那时候 `beat` 就是"刚刚播到的地方"，一旦在这里把 `_fired` 清掉，
          下一帧 `_on_tick` 会把 head 之前**每一个音**重新 emit 一遍 ——
          听感就是"一串和弦齐鸣"（已播过的音全部重响）。

        ★ 空谱面也允许走 ★
          原来这里是 `if not self.model or not self.model.notes: return`，
          于是**空谱面下时间轴根本不动**。可节拍器/记录模式恰恰要从
          空谱面开始（谱面是边记边长的），没那条时间参考就没法对着数拍。
        """
        if not self.model:
            return
        # ★ 别夹在"曲子末尾" ★
        #   原来写的是 `min(beat, self.model.total_beats)`，于是节拍器
        #   一旦走到最后一个音的末尾，每次被拉起来都会被**再次拽回末尾** ——
        #   表现就是"停在原地不动"。
        self.beat = max(0.0, beat)
        self._base = self.beat
        self._stop_at = stop_at
        # 循环要有终点才有意义（没给 `stop_at` 就不循环）
        self._loop = bool(loop) and stop_at is not None
        self._loop_from = self.beat
        if not keep_fired:
            # ★ 只把"起点之前"的音标成已响过，别整体清空 ★
            #   原来是无脑 `set()` —— 那样从曲子中间开播时，前面那些音
            #   会在头一帧被一次性补响（听感：一串和弦齐鸣）。
            #   循环也一样：每绕一圈回到起点，不该把起点之前的音再放一遍。
            self._fired = {i for i, n in enumerate(self.model.notes)
                           if n.start < self.beat - 1e-9}
        self._clock.restart()
        self.playing = True
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.playing = False
        self._loop = False

    @property
    def looping(self) -> bool:
        """正在区间循环中吗（界面用来做"再按一次就停"）。"""
        return bool(self._loop and self.playing)

    def toggle_from(self, beat: float):
        if self.playing:
            self.stop()
        else:
            self.play_from(beat)

    def _on_tick(self):
        if not self.model:
            return
        # ★★ 时间基准必须和 `TimelineEditor` 一致 ★★
        #   这里原来写的是 `spb = 60.0 / max(1, self.bpm)` —— 拿**谱面 BPM**
        #   当换算基准。可 `TimelineEditor.spb` 是**固定的 0.5**。
        #   两个基准不一样，界面就会**越走越落后**：
        #
        #       曲子标 BPM 100 → 播放器认为 1 拍 = 0.6 秒
        #       界面认为        1 拍 = 0.5 秒
        #       于是真实过了 1 秒，红线/浮窗只前进 0.833 秒 —— 落后 16.7%
        #
        #   用户报的现象就是「跟随有延迟」：浮窗高亮的音总比实际听到的慢一点，
        #   而且曲子越长落得越多。
        #
        #   统一到 `SPB` 之后就对上了：`beat = 经过秒数 / 0.5`，
        #   界面再乘回 0.5 得到秒 —— 一来一回正好是真实时间。
        spb = SPB
        self.beat = self._base + self._clock.elapsed() / 1000.0 / spb

        # ★ 循环：走到终点就折返，接着往下走 ★
        #   折返时把"超出的那一点"带上（`beat - _stop_at`），循环就接得
        #   平顺，不会每一圈都丢半帧。
        if self._loop and self._stop_at is not None \
                and self.beat >= self._stop_at:
            over = self.beat - self._stop_at
            self.beat = self._loop_from + max(0.0, over)
            self._base = self.beat
            self._clock.restart()
            # 只把"起点之前"的标成已响过 —— 起点之后的这一圈重来一遍
            self._fired = {i for i, n in enumerate(self.model.notes)
                           if n.start < self._loop_from - 1e-9}

        # 到指定的结尾就停（选区试听；循环模式下不走这条）
        if (not self._loop and self._stop_at is not None
                and self.beat >= self._stop_at):
            self.beat = self._stop_at
            self.tick.emit(self.beat)
            self.stop()
            self.finished.emit()
            return

        # ★ 记录模式（muted）下不要因为"走到曲子末尾"就停 ★
        #   用户：「节拍器有 bug 会自己停下来」。
        #   记录的时候谱面是**边记边长**的：刚开始可能只有一个音、
        #   `total_beats` 只有 1 拍 —— 于是走 0.5 秒就自己停了。
        #   静音模式是"给你一个时间参考"，该由你按「⏹ 结束」来决定何时停。
        if not self.muted and self.beat >= self.model.total_beats:
            self.beat = self.model.total_beats
            self.tick.emit(self.beat)
            self.stop()
            self.finished.emit()
            return

        for i, n in enumerate(self.model.notes):
            if n.is_rest or i in self._fired:
                continue
            if n.start <= self.beat:
                # `_fired` 照样记 —— 静音只是不出声，不改变"这个音已经过去了"
                self._fired.add(i)
                if self.muted:
                    continue        # ★ 记录模式：只走时间 ★
                for pitch in n.pitches:
                    self.note_fired.emit(pitch)

        self.tick.emit(self.beat)
