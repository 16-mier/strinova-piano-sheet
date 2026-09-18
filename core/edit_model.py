# -*- coding: utf-8 -*-
"""可编辑的谱面模型 —— 时间轴拖动改的是「间距」，改完能写回文本。

设计要点
--------
谱面在文本里是**顺序**的：每个 token 紧跟上一個。所以「音符之间的间距」
在文本里就是**一串休止符**（`-` / `^-` / `^^-` …）。

于是拖动某个音符 = 改动它前面那串休止符的总拍数。
原样保留每个音符的 `raw`（`1'&3'`、`^^1--` 这些写法一个字节都不动），
只重新生成休止符 —— 这样用户的原始谱面不会被"规范化"得面目全非。

和弦（同一时刻一起响的多个音）
------------------------------
顺序模型里两个块**永远不可能同时开始**（后一个的起点 ≥ 前一个的终点）。
所以"同时发声"只能靠**把音并进同一个 token**：`1&3&5`。
`merge_into_prev()` 干的就是这件事 —— 把后一个块的音高揉进前一个块，
两边的节奏修饰符（^ - ~）都保住。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import encode, layout
from .parser import SPB, BpmChange, Sheet

# token 的「本体」字符 —— 其余的都算节奏/装饰符
_BODY_CHARS = set("0123456789#.'")

# 一个块的时值下限（拍）—— 和 `set_dur_of` 里的下限保持一致。
# 拖动时"时值自动填满到下一个块"会用到它：贴到最近也不能是 0。
_MIN_NOTE_DUR = 0.03125

# ★ 「时间轴谱面」的秒 ↔ 拍换算在 `parser.SPB` 里定义 ★
#   （解析和模型必须用同一个常数，放两处迟早对不上）


def _fmt_sec(sec: float) -> str:
    """0.5 -> '0.5'，2.0 -> '2'，0.25 -> '0.25'（最多两位小数，去掉末尾 0）。"""
    s = ('%.2f' % sec).rstrip('0').rstrip('.')
    return s if s else '0'


# 谱面文本一行排几个音
PER_LINE_TOKENS = 4

# 记谱法一行排几个（它比 `秒:音高` 短，一行能多放几个）
PER_LINE_NOTATION = 8

# 记谱法里一个音**最多带几个修饰符**（`^` 和 `-` 加起来）。
#
# ★ 为什么要有这个上限 ★
#   能"精确表达"不等于"值得写"：`encode_duration` 对奇数分子只能拼出
#   "一堆 `^` 加一堆 `-`" —— 1.25 拍写成 `^^1----`、3.96875 拍要 132 个
#   字符。精确是精确，可读起来比 `1.25:1` 还费劲。
#   所以宁可退回时间轴格式：那种位置**本来就不是给人数的**。
NOTATION_MAX_MARKS = 5


def _layout_tokens(toks: list[str], per_line: int = PER_LINE_TOKENS) -> str:
    """把 `秒:音高` 排成"每行几个、时间右对齐"。

    ★ 为什么要对齐 ★
      时间的位数不一样（`0.5` 两个字符、`10.25` 五个），左对齐排下来
      音高那一列会参差不齐，眼睛没法竖着扫。把**时间右对齐、音高
      紧跟在冒号后面**，每列就都站齐了。

    ★ 为什么不是一行一个 ★
      一行一个确实"一个音一眼就明白"（那是当初为了治"写都粘成一团了"），
      但一首曲子几十上百个音，一行一个会把屏幕占满，更要命的是
      **看不出音的疏密** —— 哪里快、哪里慢全糊在等距的行里。
      几个一行是折中：有分行的节奏感，又能一屏看完一段。

    ★ 不影响读回来 ★
      `parser.parse` 是按空白切 token 的，中间空多少格都无所谓。
    """
    if not toks:
        return ''
    cells = []
    for t in toks:
        sec, _, pitch = t.partition(':')
        cells.append((sec, pitch))
    width = max(len(sec) for sec, _ in cells)
    rows = []
    for i in range(0, len(cells), per_line):
        rows.append('  '.join('%s:%s' % (sec.rjust(width), pitch)
                              for sec, pitch in cells[i:i + per_line]))
    return '\n'.join(rows)


def split_raw(raw: str) -> tuple[str, str, str]:
    """token -> (前导 ^, 音高本体, 尾部修饰)。

    `^^1'&3'--` -> ('^^', "1'&3'", '--')
    `1^`        -> ('', '1', '^')
    """
    n = len(raw)
    i = 0
    while i < n and raw[i] == '^':
        i += 1
    j = n
    while j > i and raw[j - 1] not in _BODY_CHARS:
        j -= 1
    return raw[:i], raw[i:j], raw[j:]


def make_raw(raw: str, pitches) -> str:
    """只换掉 token 的音高部分，节奏/装饰符一个字节不动。"""
    head, _body, tail = split_raw(raw)
    return head + '&'.join(pitches) + tail


def pitch_order(pitch: str) -> int:
    """按琴上的 PAD 序号排序 —— 和弦里的音永远从低到高写，读起来一致。"""
    pad = layout.pitch_to_pad(pitch)
    return pad if pad is not None else 999


@dataclass
class EdNote:
    """时间轴上的一个块（音符或休止符）。"""

    pitches: list[str]
    raw: str                    # 原始写法，回写时原样使用
    start: float                # 起始拍（绝对）
    dur: float                  # 时长（拍）
    is_rest: bool
    pre_bpm: list[str] = field(default_factory=list)   # 紧挨它前面的变速标记
    # ★ 显示用轨道（0 = 最下面那条）★
    #   纯粹是排版：方块等宽之后，时间上挨得近的块会互相压上，
    #   上下拖到别的轨道就能错开看清。
    #   **不影响音高、不影响发声、也不写进谱面文本** ——
    #   所以重新打开谱面时它会回到默认值（0）。
    lane: int = 0
    # 用户手动拖过轨道 → 自动排布时不再动它（但它占的位置照样算数）
    lane_fixed: bool = False

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
        # ★ 时间轴谱面（`秒:音高`）★ —— 每个音的位置自己说了算，
        #   可以向左挪、可以互相重叠。老格式做不到这些，所以两套规则。
        self.free = bool(getattr(sheet, 'free', False))
        # 只给 `move_to_beat()` 用（离线/测试里还有用）。
        # 制谱器的拖动已经不走它了 —— 那边的原则是"永远不合并"。
        self.snap_merge = 0.13
        self._build(sheet)

    # ---------------- 构建 ----------------

    def _build(self, sheet: Sheet):
        """★ 只有一种模型：时间轴谱面（每个音带绝对时间）★

        `parser._finalize_free` 保证 `sheet.free` 恒为 True ——
        老写法（`5 3 5`）进来时就已经被换算成绝对秒了，
        所以这里不用再分两套逻辑。
        """
        self._build_free(sheet)

    def _build_free(self, sheet: Sheet):
        """时间轴谱面：每个音自带**绝对秒**，位置互相独立。

        没有休止符、也没有独立时值 —— 空档就是"下一个音来得晚"，
        重叠就是"两个音排在了一起"。这正是拖动能往任意方向走的原因：
        老格式里位置是**推**出来的（前一个音 + 时值），所以向左挪
        等于要求"负间距"，数学上就不存在。
        """
        for ev in sheet.events:
            if isinstance(ev, BpmChange):
                continue
            at = getattr(ev, 'at', None)
            if at is None or not getattr(ev, 'pitches', None):
                continue
            self.notes.append(EdNote(
                pitches=list(ev.pitches), raw=ev.raw,
                start=max(0.0, float(at)) / SPB, dur=0.0, is_rest=False))
        self._sort_free()

    def _sort_free(self):
        """排好序、重算时值（= 到下一个音的距离）和总长。"""
        self.notes.sort(key=lambda n: n.start)
        for i, n in enumerate(self.notes):
            n.dur = (max(_MIN_NOTE_DUR, self.notes[i + 1].start - n.start)
                     if i + 1 < len(self.notes) else 1.0)
        self.total_beats = max((n.end for n in self.notes), default=0.0)

    # ---------------- 查询 ----------------

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
        """把第 i 个块**前面**的空档设成 beats 拍。

        🟡 **legacy（间距模型）—— 自由格式下不要用它** 🟡
          它靠 `_reflow()` 按 `dur` 从头铺，**会把后面的块整排重排**；
          而这里的谱面是"每个音带绝对时间"，`_rest_run()` 找到的休止符
          区间永远是空的 —— 所以它的净效果是"把块拉到前一个块末尾，
          然后重排它后面所有块"（方向键以前就是被这个咬到的，见 2.1）。

          制谱器已经全部改走 `move_note_free()` / `move_note_independent()`；
          现在只剩 `move_to_beat()`（离线/测试用）还在调它。
        """
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
        # ★ 先把基准写对，再让 `_reflow` 去铺 ★
        #   这一段（新休止符 + 紧跟其后的那个块）的起点必须是
        #   **前面那个块的末尾**。
        #
        #   `_reflow(from_index)` 是**保留** `notes[from_index].start`
        #   当基准的（它只往后累加），所以基准一定要在这里就写对。
        #   而 `notes[a:b] = new_items` 一删，`notes[a]` 就换人了 ——
        #   如果删掉的是休止符，换上来的正是原来第 b 个块，
        #   它的 start 还是"被推走之后"的旧值。于是 `_reflow` 拿着这个
        #   错的基准往下铺，后面整排都停在错的位置上
        #   （实测拖一个音，后面三个一起偏了 1 拍）。
        base = self.notes[a - 1].end if a > 0 else 0.0
        if a < len(self.notes):
            self.notes[a].start = base
        self._reflow(a)
        return old

    def index_of(self, note: EdNote) -> int:
        """按对象找索引 —— 拖动时索引会变，所以要用对象来定位。"""
        for i, n in enumerate(self.notes):
            if n is note:
                return i
        return -1

    def _reflow(self, from_index: int):
        """从 from_index 开始重算所有块的绝对起始拍。"""
        beat = self.notes[from_index].start if 0 <= from_index < len(
            self.notes) else 0.0
        for n in self.notes[from_index:]:
            n.start = beat
            beat += n.dur
        self.total_beats = sum(n.dur for n in self.notes)

    def remove_note(self, i: int) -> bool:
        """删掉一个块 —— 其它块**原地不动**（自由格式：位置是绝对的）。

        ★ 不是"剪切"，是"挖掉" ★
          以前这里调 `_reflow()`（按 `dur` 从头累加），删掉的那个音占的
          时间被"吃掉"，它**后面所有音整体左移** —— 而 docstring 写的是
          "它占的时间变成空档"，实现和文档正好相反。
          自由格式里每个音的 `start` 都是绝对值，所以删一个就只是
          少一个：剩下的一个都不许动。
        """
        if not (0 <= i < len(self.notes)):
            return False
        self.notes.pop(i)
        self._sort_free()
        return True

    # ---------------- 和弦（同一时刻一起响） ----------------

    def note_starting_at(self, beat: float,
                         tol: float = 0.2) -> EdNote | None:
        """起点落在 beat ± tol 内的音符块（取最近的）。"""
        best, best_d = None, 1e9
        for n in self.notes:
            if n.is_rest:
                continue
            d = abs(n.start - beat)
            if d <= tol and d < best_d:
                best, best_d = n, d
        return best

    def note_at(self, beat: float) -> EdNote | None:
        """正在覆盖 beat 那一刻的音符块。

        🟡 只有 `tests/test_core.py` 在用（生产代码走 `note_starting_at()`）。
        """
        for n in self.notes:
            if not n.is_rest and n.start - 1e-9 <= beat < n.end - 1e-9:
                return n
        return None

    def set_pitches(self, note: EdNote, pitches) -> bool:
        """重设一个块的音高（去重 + 按 PAD 序号排序 + 重写 raw）。"""
        if self.index_of(note) < 0:
            return False
        uniq: list[str] = []
        for p in pitches:
            if p and p not in uniq:
                uniq.append(p)
        if not uniq:
            return False
        note.pitches = sorted(uniq, key=pitch_order)
        note.raw = make_raw(note.raw, note.pitches)
        return True

    def add_pitch(self, note: EdNote, pitch: str) -> bool:
        """再往块里塞一个音（= 同时按下）；已经在里面就返回 False。"""
        if pitch in note.pitches:
            return False
        return self.set_pitches(note, list(note.pitches) + [pitch])

    def remove_pitch(self, note: EdNote, pitch: str) -> bool:
        """从块里拿掉一个音；拿光了整块一起删。"""
        if pitch not in note.pitches:
            return False
        left = [p for p in note.pitches if p != pitch]
        if not left:
            return self.remove_note_index(self.index_of(note))
        return self.set_pitches(note, left)

    def remove_note_index(self, i: int) -> bool:
        """按下标删块 —— 同 `remove_note()`：其它块原地不动。"""
        if not (0 <= i < len(self.notes)):
            return False
        self.notes.pop(i)
        self._sort_free()
        return True

    def merge_into_prev(self, i: int) -> EdNote | None:
        """把第 i 个块并进前一个音符 —— 变成和弦（同一时刻一起响）。

        返回合并后的那个块；合不了返回 None。
        """
        if not (0 <= i < len(self.notes)):
            return None
        src = self.notes[i]
        if src.is_rest:
            return None
        j = i - 1
        while j >= 0 and self.notes[j].is_rest:
            j -= 1
        if j < 0:
            return None
        dst = self.notes[j]
        # 1) 先删掉两者之间的休止符（空档归零）
        del self.notes[j + 1:i]
        # 2) 时值取长的那个 —— 不然音乐会莫名其妙变快
        if src.dur > dst.dur + 1e-9:
            self.set_dur_of(dst, src.dur)
        # 3) 音高揉进去
        self.set_pitches(dst, list(dst.pitches) + list(src.pitches))
        # 4) 删掉被并掉的那块
        self.notes.remove(src)
        self._reflow(j)
        return dst

    def split_pitch_out(self, note: EdNote, pitch: str) -> EdNote | None:
        """把和弦里的某个音拆出来，变成紧跟其后的独立块。"""
        i = self.index_of(note)
        if i < 0 or pitch not in note.pitches or len(note.pitches) < 2:
            return None
        dur = note.dur
        self.remove_pitch(note, pitch)
        if self.index_of(note) < 0:
            return None
        self.insert_tokens(i + 1, [encode.token_for(dur, pitch)])
        return self.notes[i + 1] if i + 1 < len(self.notes) else None

    def set_dur_of(self, note: EdNote, dur: float) -> bool:
        """改一个块的时值（重编码它的节奏部分，音高和 ~ 原样保留）。"""
        i = self.index_of(note)
        if i < 0:
            return False
        dur = max(0.03125, float(dur))
        _head, _body, tail = split_raw(note.raw)
        keep = ''.join(ch for ch in tail if ch == '~')
        c, d, actual = encode.encode_duration(dur, is_rest=False)
        note.raw = '^' * c + '&'.join(note.pitches) + keep + '-' * d
        note.dur = actual
        self._reflow(i)
        return True

    def move_to_beat(self, note: EdNote, want_start: float) -> EdNote | None:
        """把块挪到 want_start 那一拍（拖动音符走的就是这里）。

        * 落在前一个音的起点附近 -> **并成和弦**（同时发声）
        * 拖过头压到前一个音身上 -> 紧贴着它（间距归零）
        * 其它                   -> 老老实实改间距

        返回操作之后「该被选中」的块（合并时是前面那个块）。
        """
        i = self.index_of(note)
        if i < 0 or note.is_rest:
            return None
        want_start = max(0.0, float(want_start))
        j = i - 1
        while j >= 0 and self.notes[j].is_rest:
            j -= 1
        if j < 0:
            self.set_gap(i, want_start)
            return note
        prev = self.notes[j]
        if abs(want_start - prev.start) <= self.snap_merge + 1e-9:
            return self.merge_into_prev(i)
        if want_start < prev.end - 1e-9:
            self.set_gap(i, 0.0)
            return note
        self.set_gap(i, want_start - prev.end)
        return note

    def move_note_independent(self, note: EdNote, want_start: float) -> float:
        """★ 挪这一个块：位置自己说了算，**别的全都不动** ★

        因为位置是**绝对**的（每个音自己带时间），所以：

        * 向左挪可以
        * 和别的音重叠可以
        * 后面那些一律原地待着

        老的顺序记谱里位置是"前一个音 + 时值"**推**出来的，向左挪等于
        要求"负间距" —— 负的休止符 token 不存在，所以怎么调都拖不动。
        那一整套（`set_gap` / `_reflow` / 填满时值）现在只留给
        `move_to_beat()` 和单元测试，制谱器不再走。

        返回实际移动量。
        """
        i = self.index_of(note)
        if i < 0 or note.is_rest:
            return 0.0
        old = note.start
        note.start = max(0.0, float(want_start))
        self._sort_free()
        return note.start - old

    def move_note_free(self, note: EdNote, want_start: float) -> EdNote | None:
        """拖动的入口：**只挪它自己，永远不合并**。

        ★ 「不需要合并的」★
          以前这里有一档"拖到前一个音附近就并成和弦"（`snap_merge`），
          用户明确不要了 —— 撞上了也只是**挨着**，不会被吃掉变成和弦。
          挤不开的时候，时间轴会自动把它排到别的轨道去（`auto_lanes`），
          那才是"重叠"的正确处理方式。

          要写和弦走打击垫拖动多选那条路（一次给整组）。
          保留 `move_to_beat` 是因为它在离线/测试里还有用，
          但制谱器的拖动已经不走它了。
        """
        i = self.index_of(note)
        if i < 0 or note.is_rest:
            return None
        self.move_note_independent(note, want_start)
        return note

    # ---------------- 轨道自动排布 ----------------

    def auto_lanes(self, lanes: int = 6, width: float = 0.8,
                   keep_fixed: bool = True) -> int:
        """★ 让时间上重叠的方块自动落到不同轨道上 ★

        用户：「如果有重叠就放到其他轨道上」。

        规则很朴素（贪心、一遍扫完）：每个方块找**第一条**"上一个方块的
        右边缘已经过去了"的轨道放进去；全满就退到最底下那条。

        `width` 用的必须是**画出来的宽度**（制谱器的 `NOTE_W_BEAT` 拍），
        不是真实时值 —— 用户看到的挤不挤，取决于画出来的那个方块有多宽。

        `keep_fixed=True` 时，被手动拖过轨道的方块（`lane_fixed`）**不动**，
        但它占的位置照样算数，后面的方块会绕开它。

        返回挪过轨道的方块数。
        """
        last: dict[int, float] = {}
        moved = 0
        for n in self.notes:
            if n.is_rest or not n.pitches:
                continue
            x2 = n.start + width
            if keep_fixed and getattr(n, 'lane_fixed', False):
                last[n.lane] = max(last.get(n.lane, -9.0), x2)
                continue
            pick = lanes - 1
            for lane in range(lanes):
                if last.get(lane, -9.0) <= n.start + 1e-9:
                    pick = lane
                    break
            if n.lane != pick:
                n.lane = pick
                moved += 1
            last[pick] = x2
        return moved

    def add_free_note(self, start_beat: float, pitch: str) -> EdNote:
        """时间轴谱面：在指定时刻放一个**新**方块。

        ★ 不合并 ★ 用户：「连续按了还是连在一块……不需要合并，
          分别到不同的轨道上就行」。所以每次按下都新建一个方块，
          时间撞上了也不并 —— 重叠交给 `auto_lanes()` 分到不同轨道去。
        """
        n = EdNote(pitches=[pitch], raw=pitch,
                   start=max(0.0, float(start_beat)), dur=0.0, is_rest=False)
        self.notes.append(n)
        self._sort_free()
        return n

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

    def move_notes_in_range(self, start_beat: float, end_beat: float,
                            delta_beat: float) -> float:
        """★ 把落在 [start, end) 里的方块**整体平移** delta 拍 ★

        用户：「新增一个多选的办法，可以拖动多选的前后移动」。

        时间轴模型下这件事特别直接：每个音的位置都是**绝对**的，
        一起加同一个偏移就行 —— 互不影响，也不用管谁在前谁在后。

        （老格式里这根本做不到：那会儿位置是"前一个音 + 时值"推出来的，
          整段平移等于要求某个音的时值变成负数，而负的休止符 token 不存在。）

        **返回实际移动量**（贴到 0 秒时会小于请求值）—— 拖动那边要靠它
        记账：增量必须是"实际挪了多少"，不然选区会跟方块对不上。
        """
        if end_beat < start_beat:
            start_beat, end_beat = end_beat, start_beat
        hit = self.notes_in_range(start_beat, end_beat)
        if not hit:
            return 0.0
        delta = float(delta_beat)
        earliest = min(n.start for n in hit)
        if earliest + delta < 0:
            delta = -earliest                 # 贴住 0 秒，别穿到负的
        if abs(delta) < 1e-9:
            return 0.0
        for n in hit:
            n.start += delta
        self._sort_free()
        return delta

    def remove_range(self, start_beat: float, end_beat: float,
                     ripple: bool = True) -> int:
        """删掉 [start, end) 范围内的块。

        返回删掉了几个块。

        `ripple=True`（默认）**剪切式**：后面的往前接上，不留空档。
        `ripple=False` **挖掉式**：后面的原地不动，那段变成空的。

        ★ 为什么两种都要 ★
          用户：「选区内的方块全部删掉然后后面的方块可以选择向前靠齐
          或者留在原地，做俩个删除选区选项」。
          "这段不要了、后面顶上来"和"这里挖个洞、后面别动"是两件事，
          剪辑软件里也是两个不同的操作，拿一个默认值糊不过去。
        """
        if end_beat < start_beat:
            start_beat, end_beat = end_beat, start_beat
        keep = [n for n in self.notes
                if not (n.end > start_beat + 1e-9
                        and n.start < end_beat - 1e-9)]
        removed = len(self.notes) - len(keep)
        if removed:
            self.notes = keep
            if ripple:
                # 靠齐 = 从 0 开始按 `dur` 依次排开（`_reflow` 干的就是这个）
                self._reflow(0)
            else:
                # ★ 原地不动：连 `_reflow` 都不能调 ★
                #   它会按 `dur` 从头累加位置，那等于把后面的全往前拽了 ——
                #   正好是这里要避免的。总长按"最后一个块的末尾"算。
                self.total_beats = max((n.end for n in self.notes),
                                       default=0.0)
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
        """写回谱面文本。

        ★ 能写成**记谱法**就写记谱法，实在不行才退回 `秒:音高` ★
          用户：「这个铺面文本并不适合人类创作修改」。
          `秒:音高` 是给机器看的：位置绝对、无歧义，但人读起来是一串
          `0.6 / 1.2 / 1.8`，看不出节奏，改一个音还得心算秒数。
          详见 `to_notation_text()`。
        """
        body = self.to_notation_text()
        if body is None:
            body = _layout_tokens(self._free_tokens())
        return ('%s\n%s' % (self.title, body)) if self.title else body

    def _free_tokens(self) -> list[str]:
        """`秒:音高` 形式的 token（按时间排好）。"""
        toks: list[str] = []
        for n in sorted(self.notes, key=lambda x: x.start):
            if n.is_rest or not n.pitches:
                continue
            toks.append('%s:%s' % (_fmt_sec(n.start * SPB),
                                   '&'.join(n.pitches)))
        return toks

    def to_notation_text(self) -> str | None:
        """试试把它写成**记谱法**（`1 1 5 5 6 6 5`）；写不了返回 `None`。

        ★ 为什么值得做 ★
          用户：「这个铺面文本并不适合人类创作修改」。
          记谱法反过来是给人看的：`1 1 5 5` 一眼就是"四个等长的音"，
          想长一点加 `-`（`1-` = 两个音长），想快一倍加 `^`（`^1` = 半个）。
          手写一份新谱子、或者改两个音，这个写法才是顺手的。

        ★ 判定很严：有一个音对不上就**整体放弃** ★
          记谱法里的位置是"前面所有音的时值累加"出来的，能落在的
          时间点很稀疏（每拍 1 / 1.5 / 2 / 2.5 / 3 / 4 …，以及它们的
          1/2、1/4、1/8…）。从音频转出来、或者在时间轴上拖过的谱子
          通常落不到这些点上 —— 那就老老实实用时间轴格式。
          **绝不四舍五入**：差一点点就换个写法，会让谱子自己走音。
        """
        notes = [n for n in sorted(self.notes, key=lambda x: x.start)
                 if not n.is_rest and n.pitches]
        if not notes:
            return None
        # 记谱法的第一个音必然在 0（`parser._finalize_free` 里 `t` 从 0 起）
        if abs(notes[0].start) > 1e-9:
            return None
        toks: list[str] = []
        for i, n in enumerate(notes):
            body = '&'.join(n.pitches)
            if i + 1 >= len(notes):
                # 最后一个音没有"下一个"可参照，它的时值怎么写都不影响
                # 别人的位置 —— 写一个 `1`（1 拍）收尾就行。
                toks.append(body)
                break
            gap = notes[i + 1].start - n.start          # 到下一个音的间隔（拍）
            if gap <= 0:
                return None                             # 同位置的没并成和弦
            carets, dashes, actual = encode.encode_duration(gap, is_rest=False)
            # ★ 容差取 1e-6 拍（= 0.5 微秒）★
            #   再严就会被浮点误差误伤（`0.3 / 0.5` 是 `0.5999…`）；
            #   再松就可能把"1.2 拍"这种硬塞成"1 拍 + 噪音"。
            if abs(actual - gap) > 1e-6:
                return None
            # ★ 还有一道"能写但没法看"的门槛 ★
            #   详见 `NOTATION_MAX_MARKS`：奇数分子会拼出一长串修饰符，
            #   那种写法人根本不想读，不如退回 `秒:音高`。
            if carets + dashes > NOTATION_MAX_MARKS:
                return None
            toks.append('^' * carets + body + '-' * dashes)
        # ★ 一行别塞太多 ★
        #   全挤成一行的话，长曲子会变成一条望不到头的长龙
        #   （用户之前就抱怨过「写都粘成一团了」）。
        #   按个数分行：一行 8 个，读起来有节奏感，也方便对着数。
        rows = [' '.join(toks[i:i + PER_LINE_NOTATION])
                for i in range(0, len(toks), PER_LINE_NOTATION)]
        text = '\n'.join(rows)

        # ★ 自己先读一遍，逐音核对 ★
        #   记谱法的位置是"从 0 累加"出来的，时间轴的位置是"绝对值" ——
        #   两套东西之间只要有一丝对不上（浮点、边界、以后谁改了
        #   `parser` 的行为、`encode_duration` 在极小值上的兜底……），
        #   写出去的谱子就会**静默走音**：用户看到的是"位置莫名其妙
        #   变了"，而且很难追到这儿来。
        #   所以把刚拼好的文本 parse 回来、逐个比一遍，
        #   差一点点就整体放弃、退回 `秒:音高`。
        #   代价是回写时多解析一次（几百个音，毫秒级），
        #   换的是"绝不悄悄写错谱子"。
        from .parser import parse as _parse
        try:
            back = [c.at for c in _parse(text).chords if not c.is_rest]
        except Exception:
            return None
        want = [n.start * SPB for n in notes]
        if len(back) != len(want):
            return None
        if any(abs(a - b) > 1e-9 for a, b in zip(back, want)):
            return None
        return text

    def to_free_text(self) -> str:
        """导成时间轴格式（`秒:音高`）—— **不管它原来是什么格式**。

        位置取每个块**现在**的 `start`，所以转换只改写法、不改时间：
        旧谱子转过来之后，每个音还落在原来的位置。

        ★ `rebuild()` 只在记谱法写不出来时才走这里 ★
          排版是"每行 4 个、时间右对齐"，理由见 `_layout_tokens`
          （用户：「这个能做的更好读懂吗」）。
        """
        body = _layout_tokens(self._free_tokens())
        return ('%s\n%s' % (self.title, body)) if self.title else body

    # ---------------- 统计 ----------------

    def stats(self) -> str:
        audio = self.audio_notes()
        if self.free:
            return ('时间轴谱面 · %d 个音 · %.2f 秒'
                    % (len(audio), self.total_beats * SPB))
        rest = [n for n in self.notes if n.is_rest]
        return ('%d 个音 · %d 个休止 · 共 %.2f 秒'
                % (len(audio), len(rest), self.total_beats * SPB))
