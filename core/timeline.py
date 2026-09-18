# -*- coding: utf-8 -*-
"""时间轴 —— 把谱子编译成「第几秒到第几秒弹哪个键」。

记谱里的时长单位是**拍**，而播放要用**秒**，两者靠 BPM 换算：
    秒数 = 拍数 × 60 / BPM

变速标记 `#120#` 会在它出现的位置生效，影响**它之后**的所有音符 ——
所以必须按事件顺序边扫边换 BPM，不能一次性算完。
"""

from __future__ import annotations

from dataclasses import dataclass

from .parser import BpmChange, Chord, Sheet

DEFAULT_BPM = 120


@dataclass
class TimedChord:
    """一个已定位到时间轴上的和弦。"""

    chord: Chord
    index: int          # 在谱子里是第几个和弦（不含 BPM 事件）
    start_beat: float
    start_sec: float
    end_sec: float
    bpm: int            # 弹这个音时的 BPM

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


class Timeline:
    """编译后的谱子，供界面按时间查询。"""

    def __init__(self, sheet: Sheet, default_bpm: int = DEFAULT_BPM):
        self.sheet = sheet
        self.items: list[TimedChord] = []
        self.bpm_points: list[tuple[float, int]] = []   # (秒, BPM)

        bpm = default_bpm
        t = 0.0
        beat = 0.0
        idx = 0

        # bpm_points 完全由事件流填充；曲子开头的 #BPM# 覆盖默认值

        for ev in sheet.events:
            if isinstance(ev, BpmChange):
                if ev.bpm > 0:
                    bpm = ev.bpm
                    # 同一个时间点重复出现直接覆盖，避免 (0,120)(0,100) 这种脏数据
                    if self.bpm_points and self.bpm_points[-1][0] == t:
                        self.bpm_points[-1] = (t, bpm)
                    else:
                        self.bpm_points.append((t, bpm))
                continue

            assert isinstance(ev, Chord)
            # ★ 位置直接读 `at`（绝对秒）★
            #   `parser._finalize_free` 已经把两种写法都摊平成这个模型了。
            #   时值还是"拍"（下游一直用这个单位），按当时的 BPM 折算成秒。
            at = getattr(ev, 'at', None)
            start_sec = t if at is None else float(at)
            dur_sec = max(0.0, ev.duration) * 60.0 / bpm
            self.items.append(TimedChord(
                chord=ev, index=idx,
                start_beat=start_sec / max(1e-9, 60.0 / bpm),
                start_sec=start_sec, end_sec=start_sec + dur_sec,
                bpm=bpm,
            ))
            t = start_sec + dur_sec
            beat += ev.duration
            idx += 1

        if not self.bpm_points:
            self.bpm_points.append((0.0, default_bpm))
        elif self.bpm_points[0][0] > 0.0:
            self.bpm_points.insert(0, (0.0, default_bpm))

        self.total_sec = t
        self.total_beats = beat

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    # ---------- 按时间查询 ----------

    def index_at(self, sec: float) -> int:
        """当前正在第几个音符（已过完的算前一个）。空谱返回 -1。"""
        if not self.items:
            return -1
        lo, hi = 0, len(self.items) - 1
        if sec < self.items[0].start_sec:
            return -1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.items[mid].start_sec <= sec:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def item_at(self, sec: float) -> TimedChord | None:
        i = self.index_at(sec)
        return self.items[i] if i >= 0 else None

    def upcoming(self, sec: float, count: int = 6) -> list[TimedChord]:
        """从当前时刻起，往后数 count 个待弹的音（含当前正在响的那个）。"""
        if not self.items:
            return []
        start = self.index_at(sec)
        if start < 0:
            start = 0
        return self.items[start:start + count]

    def bpm_at(self, sec: float) -> int:
        bpm = self.bpm_points[0][1] if self.bpm_points else DEFAULT_BPM
        for at, value in self.bpm_points:
            if at <= sec:
                bpm = value
            else:
                break
        return bpm

    def seconds_per_beat(self, sec: float = 0.0) -> float:
        return 60.0 / max(1, self.bpm_at(sec))

    # ---------- 统计 ----------

    def stats(self) -> str:
        if not self.items:
            return '空谱'
        # ★ 只报秒 ★ —— 用户：「完全按照时间轴来，去掉节拍这个东西」。
        #   BPM 还留着（谱面里本来就标了），但它只是记谱时的历史信息，
        #   不代表任何东西落在哪一秒上。
        return ('%d 个音 · 共 %.1f 秒 · BPM %d%s'
                % (len(self.items), self.total_sec,
                   self.bpm_points[0][1],
                   '（含变速）' if len(self.bpm_points) > 1 else ''))

    def all_pitches(self) -> list[str]:
        out = []
        for it in self.items:
            out.extend(it.chord.pitches)
        return out


def timeline_from_notes(notes, bpm: int = DEFAULT_BPM) -> Timeline:
    """从「时间轴谱面」的音符列表**直接**搭一个 Timeline，不经过文本。

    ★ 契约：`notes[i].start` 必须是**【秒】**，不是【拍】★

      这条以前没写清楚，于是真的出过事（2026-xx）：
      制谱器那边的 `EdNote.start` 单位是**拍**（`core/edit_model.py` 里
      `start = 秒 / SPB`），却被原样递了进来。结果浮窗拿到的时间轴
      整体**放大一倍**（demo.txt：主界面 28.8 秒 vs 制谱器 55.2 秒），
      而喂给它的播放位置是真实秒 —— 浮窗只走到"已播放时长的一半"，
      越弹越落后，用户看到的就是「都下俩个按键了显示还是上俩个」。

      修法是在**调用方**换算（`ui/editor.py` 的 `_SecNote`）——
      因为这个函数**故意不 import `SPB`**（见下），
      所以单位换算只能由交出数据的一方负责。

    ★ 为什么要有这条近路 ★
      制谱器里的模型（`EditModel.notes`）本来就是"位置 + 音高"，
      而浮窗要的也只是"此刻该打哪些键、后面几个是什么"。
      绕回去把文本 `parse()` 一遍纯属浪费 —— 那还是每 12 ms 一次。

    ★ 时值一律记 0 ★
      浮窗只看**起点**（这个音什么时候该打），不看它响多久 ——
      制谱器里每个方块的长度本来就自动铺满到下一个音。

    只用到 `.start` / `.pitches` / `.is_rest` / `.label` 四个属性，
    所以这里不用导入 `edit_model`，也就不会有循环依赖。
    """
    sheet = Sheet()
    for n in notes:
        if getattr(n, 'is_rest', False):
            continue
        pitches = list(getattr(n, 'pitches', []) or [])
        if not pitches:
            continue
        sheet.events.append(Chord(
            pitches=pitches, duration=0.0, is_rest=False,
            raw=getattr(n, 'label', None) or '+'.join(pitches),
            at=float(getattr(n, 'start', 0.0))))
    return Timeline(sheet, default_bpm=bpm)
