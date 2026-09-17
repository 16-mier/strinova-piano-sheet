# -*- coding: utf-8 -*-
"""可编辑的谱面模型 —— 时间轴拖动改的是「间距」，改完能写回文本。

设计要点
--------
谱面在文本里是**顺序**的：每个 token 紧跟上一個。所以「音符之间的间距」
在文本里就是**一串休止符**（`-` / `^-` / `^^-` …）。

于是拖动某个音符 = 改动它前面那串休止符的总拍数。
原样保留每个音符的 `raw`（`1'&3'`、`^^1--` 这些写法一个字节都不动），
只重新生成休止符 —— 这样用户的原始谱面不会被"规范化"得面目全非。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import encode
from .parser import BpmChange, Sheet


def is_bpm_token(tok: str) -> bool:
    return (tok.startswith('#') and tok.endswith('#')
            and len(tok) > 2 and tok[1:-1].isdigit())


@dataclass
class EdNote:
    """时间轴上的一个块（音符或休止符）。"""

    pitches: list[str]
    raw: str                    # 原始写法，回写时原样使用
    start: float                # 起始拍（绝对）
    dur: float                  # 时长（拍）
    is_rest: bool
    pre_bpm: list[str] = field(default_factory=list)   # 紧挨它前面的变速标记

    @property
    def end(self) -> float:
        return self.start + self.dur

    @property
    def label(self) -> str:
        return '休止' if self.is_rest else '+'.join(self.pitches)


class EditModel:
    """时间轴编辑器背后的数据。"""

    def __init__(self, sheet: Sheet):
        self.title = sheet.title
        self.notes: list[EdNote] = []
        self.total_beats = 0.0
        self._build(sheet)

    # ---------------- 构建 ----------------

    def _build(self, sheet: Sheet):
        beat = 0.0
        pending_bpm: list[str] = []
        for ev in sheet.events:
            if isinstance(ev, BpmChange):
                pending_bpm.append('#%d#' % ev.bpm)
                continue
            self.notes.append(EdNote(
                pitches=list(ev.pitches), raw=ev.raw,
                start=beat, dur=ev.duration, is_rest=ev.is_rest,
                pre_bpm=pending_bpm))
            pending_bpm = []
            beat += ev.duration
        self.total_beats = beat

    # ---------------- 查询 ----------------

    def gap_before(self, i: int) -> float:
        """第 i 个块前面那串休止符的总拍数（没有就是 0）。"""
        if i <= 0 or i >= len(self.notes):
            return 0.0
        total = 0.0
        j = i - 1
        while j >= 0 and self.notes[j].is_rest:
            total += self.notes[j].dur
            j -= 1
        return total

    def _rest_run(self, i: int) -> tuple[int, int]:
        """第 i 个块前面连续休止符的区间 [start, end)。"""
        j = i - 1
        while j >= 0 and self.notes[j].is_rest:
            j -= 1
        return j + 1, i

    def audio_notes(self) -> list[EdNote]:
        return [n for n in self.notes if not n.is_rest]

    # ---------------- 编辑 ----------------

    def set_gap(self, i: int, beats: float):
        """把第 i 个块**前面**的空档设成 beats 拍。"""
        if not (0 <= i < len(self.notes)):
            return
        beats = max(0.0, beats)
        a, b = self._rest_run(i)
        old = sum(n.dur for n in self.notes[a:b])

        # 新的休止符块
        new_raws = encode.split_gap(beats) if beats > 1e-6 else []
        new_items = []
        cursor = self.notes[a].start if a < len(self.notes) else 0.0
        for raw in new_raws:
            from .parser import token_duration
            dur, _ = token_duration(raw)
            new_items.append(EdNote(pitches=[], raw=raw, start=cursor,
                                    dur=dur, is_rest=True))
            cursor += dur

        self.notes[a:b] = new_items
        self._reflow(a)
        return old

    def index_of(self, note: EdNote) -> int:
        """按对象找索引 —— 拖动时索引会变，所以要用对象来定位。"""
        for i, n in enumerate(self.notes):
            if n is note:
                return i
        return -1

    def set_gap_of(self, note: EdNote, beats: float) -> bool:
        i = self.index_of(note)
        if i < 0:
            return False
        self.set_gap(i, beats)
        return True

    def gap_of(self, note: EdNote) -> float:
        i = self.index_of(note)
        return self.gap_before(i) if i >= 0 else 0.0

    def move_note(self, i: int, delta: float):
        """把第 i 个块往右（正）或往左（负）挪 delta 拍 —— 只改它前面的空档。

        返回真实移动量（受前面的音符边界限制）。
        """
        if not (0 <= i < len(self.notes)) or self.notes[i].is_rest:
            return 0.0
        cur = self.gap_before(i)
        want = max(0.0, cur + delta)
        # 不能挤到左边的音符身上
        self.set_gap(i, want)
        j = self._index_of_note_after(i)
        if j < 0:
            return 0.0
        return self.gap_before(j) - cur

    def _index_of_note_after(self, i: int) -> int:
        """set_gap 之后，原来第 i 个块现在在哪。"""
        for k in range(i, len(self.notes)):
            if not self.notes[k].is_rest:
                return k
        return -1

    def _reflow(self, from_index: int):
        """从 from_index 开始重算所有块的绝对起始拍。"""
        beat = self.notes[from_index].start if 0 <= from_index < len(
            self.notes) else 0.0
        for n in self.notes[from_index:]:
            n.start = beat
            beat += n.dur
        self.total_beats = sum(n.dur for n in self.notes)

    def remove_note(self, i: int):
        """删掉一个块（它占的时间变成空档，交给后面的休止符逻辑处理）。"""
        if not (0 <= i < len(self.notes)):
            return
        self.notes.pop(i)
        self._reflow(max(0, i - 1))

    # ---------------- 区域操作 ----------------

    def notes_in_range(self, start_beat: float,
                       end_beat: float) -> list[EdNote]:
        """落在 [start, end) 里的块（跟区间有重叠就算）。"""
        if end_beat < start_beat:
            start_beat, end_beat = end_beat, start_beat
        out = []
        for n in self.notes:
            if n.end > start_beat + 1e-9 and n.start < end_beat - 1e-9:
                out.append(n)
        return out

    def remove_range(self, start_beat: float, end_beat: float) -> int:
        """删掉 [start, end) 范围内的块，后面的内容往前接上。

        返回删掉了几个块。这是「剪切式」删除：不留空档。
        """
        if end_beat < start_beat:
            start_beat, end_beat = end_beat, start_beat
        keep = [n for n in self.notes
                if not (n.end > start_beat + 1e-9
                        and n.start < end_beat - 1e-9)]
        removed = len(self.notes) - len(keep)
        if removed:
            self.notes = keep
            self._reflow(0)
        return removed

    def clear_all(self):
        """全删。"""
        self.notes = []
        self.total_beats = 0.0

    def rest_index_after_beat(self, beat: float) -> int:
        """找一个合适的插入位置索引（返回第一个起始拍 >= beat 的块下标）。"""
        for i, n in enumerate(self.notes):
            if n.start >= beat - 1e-9:
                return i
        return len(self.notes)

    def insert_tokens(self, index: int, tokens: list[str]) -> int:
        """在指定下标处插入一批 token，返回插入的块数。"""
        from .parser import token_duration
        if not tokens:
            return 0
        index = max(0, min(index, len(self.notes)))
        base = (self.notes[index].start if index < len(self.notes)
                else self.total_beats)
        items = []
        cursor = base
        for tok in tokens:
            dur, is_rest = token_duration(tok)
            body = tok
            for ch in ('^', '-', '~'):
                body = body.replace(ch, '')
            pitches = [p for p in body.split('&') if p] if not is_rest else []
            items.append(EdNote(pitches=pitches, raw=tok, start=cursor,
                                dur=dur, is_rest=is_rest))
            cursor += dur
        self.notes[index:index] = items
        self._reflow(index)
        return len(items)

    # ---------------- 回写 ----------------

    def rebuild(self) -> str:
        """生成记谱文本。标题单独一行，音符按每 8 个换行，方便读。"""
        toks: list[str] = []
        for n in self.notes:
            toks.extend(n.pre_bpm)
            toks.append(n.raw)
        lines = [' '.join(toks[i:i + 8]) for i in range(0, len(toks), 8)]
        body = '\n'.join(lines)
        return ('%s\n%s' % (self.title, body)) if self.title else body

    # ---------------- 试听用 ----------------

    def sec_table(self, bpm: int = 120) -> list[tuple[float, EdNote]]:
        """[(起始秒, 块)]，给时间轴编辑器的播放头用（单一 BPM）。"""
        spb = 60.0 / max(1, bpm)
        return [(n.start * spb, n) for n in self.notes]

    def stats(self) -> str:
        audio = self.audio_notes()
        rest = [n for n in self.notes if n.is_rest]
        return ('%d 个音 · %d 个休止 · 共 %.2f 拍'
                % (len(audio), len(rest), self.total_beats))
