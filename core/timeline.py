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
            dur_sec = ev.duration * 60.0 / bpm
            self.items.append(TimedChord(
                chord=ev, index=idx,
                start_beat=beat, start_sec=t, end_sec=t + dur_sec,
                bpm=bpm,
            ))
            t += dur_sec
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
        return ('%d 个音 · 共 %.1f 拍 · 约 %.1f 秒 · BPM %d%s'
                % (len(self.items), self.total_beats, self.total_sec,
                   self.bpm_points[0][1],
                   '（含变速）' if len(self.bpm_points) > 1 else ''))

    def all_pitches(self) -> list[str]:
        out = []
        for it in self.items:
            out.extend(it.chord.pitches)
        return out
