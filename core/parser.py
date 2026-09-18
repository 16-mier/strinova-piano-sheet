# -*- coding: utf-8 -*-
"""卡拉彼丘琴谱 —— 记谱法解析器。

语法完全兼容 StrinovaPracticeRoom（猫弹琴 2.8.0），现成曲谱可直接用。

记谱速查
--------
音高    中音 1~7 ｜ 半音 #4 ｜ 低音 6. ｜ 高音 3' ｜ 倍高音 1''
节奏    基础 = 1 拍
        `-` 加法：每个 +1 拍
        `^` 乘法：每个 ×0.5（写在音符前或后都行）
        例：1-  = 2 拍      ^1   = 0.5 拍
            ^^1 = 0.25 拍   ^1-- = 1.5 拍（附点四分）
            ^^1-- = 0.75 拍（附点八分）
休止    单独写 `-` 是休止 1 拍，`^-` 是休止 0.5 拍
和弦    `&` 连接同时发声：1'&3'&5'，可带节奏 ^1&3&5
变速    `#数字#` 在任意位置实时改 BPM，如 #156#
排版    空格 / 换行只是排版，会被跳过
        `~` 是延音记号，不影响时长
        「不像乐谱的文字」会被整段跳过，且**不当作休止符**（不留空档）
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 合法字符白名单 —— 与猫弹琴的 _is_valid_note 完全一致
ALLOWED_CHARS = set("0123456789^'-~\"&#.")

# 计算时值前要剥离的修饰符
_STRIP_FOR_BODY = ("^", "-", "~", "&")


@dataclass
class Chord:
    """一个和弦，或一个休止符。"""

    pitches: list[str]        # 和弦内各音，如 ['1', "3'"]；休止符为 []
    duration: float           # 拍数
    is_rest: bool
    raw: str                  # 原始 token（含修饰符）
    line: int = 0
    col: int = 0
    # ★ 「时间轴谱面」专用：这个音的**绝对开始时间（秒）** ★
    #   `None` = 老格式（顺序记谱，位置靠前面的累计时值推出来）。
    #   不是 None 时，位置就是它自己说了算 —— 于是可以向左挪、
    #   可以和别的音重叠，这些老格式在数学上就表达不了。
    at: float | None = None

    def __str__(self) -> str:
        if self.is_rest:
            # 不带单位 —— `duration` 内部是"拍"，但界面上不出现这个字。
            return '休止%s' % _fmt(self.duration)
        return '+'.join(self.pitches)


@dataclass
class BpmChange:
    """曲子中途的变速标记。"""

    bpm: int
    line: int = 0
    col: int = 0


def _fmt(x: float) -> str:
    """0.25 -> '0.25'，2.0 -> '2'（去掉多余的小数点）。"""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ('%g' % x)


def is_valid_token(token: str) -> bool:
    """token 是否只由白名单字符组成。"""
    if not token:
        return False
    return all(c in ALLOWED_CHARS for c in token)


def token_duration(token: str) -> tuple[float, bool]:
    """算一个 token 的时长。返回 (拍数, 是否休止)。

    引擎核心：**先加法，后乘法**
        基础时长 = 有音符本体 ? 1.0 : 0.0
        每个 '-' 加 1.0 拍
        每个 '^' 把总时长砍一半
    """
    body = token
    for ch in _STRIP_FOR_BODY:
        body = body.replace(ch, '')

    has_note = any(c.isdigit() for c in body)
    duration = 1.0 if has_note else 0.0

    duration += float(token.count('-'))
    for _ in range(token.count('^')):
        duration *= 0.5

    return duration, (not has_note)


def split_chord(pitches_text: str) -> list[str]:
    """把 '1&3&5' 拆成 ['1','3','5']；单音就返回单元素列表。"""
    return [p for p in pitches_text.split('&') if p]


@dataclass
class Sheet:
    """一份谱子：和弦 / 变速标记的有序事件流。"""

    events: list = field(default_factory=list)
    source: str = ''
    title: str = ''
    # ★ 是不是「时间轴谱面」★
    #   True  = 每个音自己带绝对时间（`秒:音高`），位置互相独立，
    #           可以向左挪、可以重叠。制谱器按这个决定用哪套编辑规则。
    #   False = 老格式（顺序记谱），位置靠前面的累计时值推出来。
    free: bool = False

    # ---- 便捷视图 ----

    @property
    def chords(self) -> list[Chord]:
        return [e for e in self.events if isinstance(e, Chord)]

    @property
    def has_chords(self) -> bool:
        return any(isinstance(e, Chord) for e in self.events)

    def __len__(self) -> int:
        return len(self.chords)

    def __bool__(self) -> bool:
        return bool(self.chords)

    def describe(self, limit: int = 0) -> str:
        """人类可读的预览，用于调试 / 界面显示。"""
        lines = []
        if self.title:
            lines.append('标题: %s' % self.title)
        n = 0
        for ev in self.events:
            if isinstance(ev, BpmChange):
                lines.append('  BPM -> %d' % ev.bpm)
            else:
                tag = '休止' if ev.is_rest else '+'.join(ev.pitches)
                lines.append('  %-12s %s 拍' % (tag, _fmt(ev.duration)))
            n += 1
            if limit and n >= limit:
                lines.append('  ...')
                break
        return '\n'.join(lines)


DEFAULT_BPM = 120

# ★ 时间轴谱面的秒 ↔ 拍换算 ★
#   新写法（`秒:音高`）直接写秒，而 `Chord.duration` 这类老字段仍然按"拍"。
#   取 0.5 = BPM 120 —— **纯粹是个换算系数**，跟演奏速度无关。
#
#   ★ 但界面上**不再出现「拍」**★
#     用户：「完全按照时间轴来，去掉节拍这个东西」。
#     下面这个 0.5 只活在代码内部；凡是给人看的地方一律显示**秒**
#     —— 见 `ui/editor.py` 的 `lbl_pos` 和 `EditModel.stats()`。
#     所以读到这里别把它当成"用户概念里的拍"，它就是个 ×2 的刻度，
#     跟曲子标不标 `#120#` 也没有任何关系。
SPB = 0.5


def _finalize_free(sheet: 'Sheet') -> None:
    """★ 把整篇统一成「时间轴谱面」：位置一律是**绝对秒** ★

    老写法（`5 3 5`、`1 - 2`）在这里被换算成秒 —— 于是「旧格式」
    只是一种**输入写法**，进来之后内部只有一种模型：每个音自己带
    绝对时间，编辑器可以随便左右拖、随便重叠。

    为什么非得在这一层摊平：老写法的位置是"前一个音 + 时值"**推**出来的，
    向左挪等于要求"负间距"，而负的休止符 token 不存在 —— 数学上就做不到。
    """
    # ★ 按**秒**往前走，不要按拍累加 ★
    #   拍速会被 `#BPM#` 改掉，而"这个音在第几秒"取决于**它当时**的 BPM。
    #   原来写成 `at = beat * (60/bpm)`，于是 `#60# 1 #120# 1` 里
    #   第二个音被按 120 折算成 0.5 秒 —— 而它其实在 1.0 秒
    #   （第一个音在 60 BPM 下占满 1 秒）。
    t = 0.0
    bpm = DEFAULT_BPM
    ch: list[Chord] = []
    bpms: list[int] = []
    for ev in sheet.events:
        if isinstance(ev, BpmChange):
            if ev.bpm > 0:
                bpm = ev.bpm
            continue
        if not isinstance(ev, Chord):
            continue
        if ev.at is None:
            ev.at = t
        t = ev.at + ev.duration * (60.0 / max(1, bpm))
        ch.append(ev)
        bpms.append(bpm)
    # 时值仍然按**拍**（`Timeline` 等下游用的就是这个单位）：
    # 拿相邻两个音的绝对秒差、按**它当时的 BPM** 折回来。
    #   ★ 别用全局 SPB ★ —— 那是 BPM 120 的系数，
    #   碰到 `#60#` 会算成"1 拍 = 2 拍"。
    #   ★ 最后一个音保留它原本的时值 ★ —— 老写法里 token 自带
    #   （`1-` 就是 2 拍）；新写法里没有"下一个音"可参照，
    #   给 1 拍收尾即可。原来无脑覆盖成 1 拍，会把 `#180# ^1 ^2 ^3`
    #   这种三连音的总长度算错。
    for i, c in enumerate(ch):
        if i + 1 < len(ch):
            spb = 60.0 / max(1, bpms[i])
            c.duration = max(0.0, (ch[i + 1].at - c.at) / spb)
        elif c.duration <= 0:
            c.duration = 1.0
    sheet.free = True


def _is_bpm_token(token: str) -> bool:
    """#120# 这种形式的变速标记。"""
    return (token.startswith('#') and token.endswith('#')
            and len(token) > 2)


def parse(text: str) -> Sheet:
    """把记谱文本解析成 Sheet。

    规则与猫弹琴的 _parse_score_with_positions 一致：
      * 按空白切 token
      * `#数字#` 是变速标记
      * 纯 `^` 的 token 攒成前缀，拼到下一个音符前面
      * 非法字符的 token 整段跳过
      * 非乐谱文字跳过且**不留时间空档**
    """
    sheet = Sheet(source=text)
    events: list = sheet.events

    pending_prefix = ''
    line = 0
    col = 0
    i = 0
    length = len(text)

    # 标题：第一行里不含乐谱字符的整行文本
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped and not any(c in ALLOWED_CHARS for c in stripped):
            sheet.title = stripped
            break

    while i < length:
        # --- 跳过空白，同时维护行列号 ---
        while i < length:
            c = text[i]
            if c == '\n':
                line += 1
                col = 0
                i += 1
            elif c in ' \t\r':
                col += 1
                i += 1
            else:
                break

        if i >= length:
            break

        start_line = line
        start_col = col
        start_index = i

        # --- 读一个 token（到空白为止）---
        while i < length and text[i] not in ' \t\n\r':
            i += 1
            col += 1

        token = text[start_index:i]

        # --- 变速标记 ---
        if _is_bpm_token(token):
            pending_prefix = ''
            bpm_str = token[1:-1]
            if bpm_str.isdigit():
                events.append(BpmChange(bpm=int(bpm_str),
                                       line=start_line, col=start_col))
            continue

        # --- ★ 时间轴格式：`秒:音高` ★ ---
        #   例：`0:5 0.25:3 1:1'&3' 2.5:2'`
        #   冒号左边是**绝对开始时间（秒）**，右边还是老记谱法的音高。
        #   一旦出现这种 token，整篇就按"时间轴谱面"处理（`sheet.free`）——
        #   每个音的位置自己说了算，不再由前面的音累加推算。
        #   放在 `is_valid_token` 之前，因为 `:` 不在白名单里。
        if ':' in token:
            head, _, tail = token.partition(':')
            try:
                at = float(head)
            except ValueError:
                at = None
            if at is not None:
                sheet.free = True
                pending_prefix = ''
                body = tail
                for ch in ("^", "-", "~"):
                    body = body.replace(ch, '')
                pitches = split_chord(body)
                if pitches:
                    events.append(Chord(pitches=pitches, duration=0.0,
                                        is_rest=False, raw=token,
                                        line=start_line, col=start_col,
                                        at=at))
                continue

        # --- 合法性校验：非法就整段跳过，不留空档 ---
        if not is_valid_token(token):
            pending_prefix = ''
            continue

        # --- 纯 '^' 的 token 攒起来当前缀 ---
        if token.replace('^', '') == '':
            pending_prefix += token
            continue

        full = pending_prefix + token
        pending_prefix = ''

        duration, is_rest = token_duration(full)

        body = full
        for ch in ("^", "-", "~"):
            body = body.replace(ch, '')
        pitches = split_chord(body)

        events.append(Chord(pitches=pitches, duration=duration,
                            is_rest=is_rest, raw=full,
                            line=start_line, col=start_col))

    # ★ 统一成绝对时间（时间轴谱面）★
    #   老写法在这里被换算成秒，之后内部只有一种模型。
    _finalize_free(sheet)
    return sheet


def load(path: str) -> Sheet:
    """从文件读一份谱子（自动尝试 UTF-8 / GBK）。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'utf-16'):
        try:
            return parse(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return parse(raw.decode('utf-8', 'replace'))


def save(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(text)
