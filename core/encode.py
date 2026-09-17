# -*- coding: utf-8 -*-
"""拍数 → 记谱写法 的**反向编译**。

时间轴编辑器里拖动音符之后，要把新的间隔写回文本谱面，
而文本格式的表达能力有限（只有 `-` 加一拍、`^` 砍一半），
所以需要一个"给定拍数，找出最接近的合法写法"的算法。

    token = '^'*c + 音符本体 + '-'*d
    (1 + d) / 2^c  = 实际拍数

休止符没有本体：

    token = '^'*c + '-'*d      (d >= 1)
    d / 2^c = 实际拍数
"""

from __future__ import annotations

MAX_CARETS = 5      # 最多几个 ^，防止冒出 ^^^^^1 这种鬼东西


def encode_duration(beats: float, is_rest: bool = False,
                    max_carets: int = MAX_CARETS) -> tuple[int, int, float]:
    """把拍数编码成 (carets, dashes, 实际拍数)。

    调用方按 `'^'*carets + 本体 + '-'*dashes` 拼 token（休止符没有本体）。
    """
    beats = max(0.0, float(beats))
    best = None

    for c in range(max_carets + 1):
        scale = float(1 << c)
        units = beats * scale
        if is_rest:
            d = int(round(units))
            if d < 1:
                continue
            actual = d / scale
        else:
            d = int(round(units)) - 1
            if d < 0:
                continue
            actual = (1 + d) / scale
        err = abs(actual - beats)
        if best is None or err < best[0] - 1e-12:
            best = (err, c, d, actual)

    if best is None:
        return (0, 1, 1.0) if is_rest else (0, 0, 1.0)

    _err, c, d, actual = best
    return c, d, actual


def token_for(beats: float, body: str = '',
              max_carets: int = MAX_CARETS) -> str:
    """直接拼出 token。body 为空时按休止符处理。"""
    is_rest = not body
    c, d, _ = encode_duration(beats, is_rest, max_carets)
    return '^' * c + body + '-' * d


def encode_rest(beats: float, max_carets: int = MAX_CARETS) -> str:
    return token_for(beats, '', max_carets)


def split_gap(beats: float, max_rest: float = 4.0) -> list[str]:
    """把一段**空档**拆成若干休止符（单个休止符太长不好读）。

    例如 5.5 拍 -> ['1-', '^-', '1'] 之类，总和尽量等于 beats。
    """
    out: list[str] = []
    left = beats
    guard = 0
    while left > 1e-9 and guard < 64:
        guard += 1
        if left <= max_rest + 1e-9:
            out.append(encode_rest(left))
            left = 0.0
            break
        out.append(encode_rest(max_rest))
        left -= max_rest
    if left > 1e-9:
        out.append(encode_rest(left))
    return [t for t in out if t]
