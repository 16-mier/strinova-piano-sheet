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

    def __str__(self) -> str:
        if self.is_rest:
            return '休止%s拍' % _fmt(self.duration)
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
