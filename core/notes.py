# -*- coding: utf-8 -*-
"""★ 音名 → 频率 / 采样文件 ★ —— 琴上那 16 个键的"身份表"。

★ 为什么单独有这么个模块 ★
  这两张表原本长在 `core/transcribe.py`（听音记谱）里，但**它们不是听音**：

    · `core/synth.py`   合成音色要按频率生成
    · `core/render.py`  把谱面渲染成 wav（给「🎙 播音到游戏麦克风」）也要
    · `ui/views.py`     判断"哪两个键同音高"（`8` 和 `1'`）也要

  听音那整套删掉之后，上面三个还得用，所以把表挪出来单独立个户头。

★ 基准音为什么是 C3，不是通常的 C4 ★
  从 16 个游戏原始采样里量出来的（`tools/check_notes.py`）：

      PAD1  (`1`)    实测 130 Hz = C3
      PAD8  (`8`)    实测 259 Hz = C4
      PAD9  (`1'`)   实测 259 Hz = C4   ← 和 `8` 同音，游戏里是两个键
      PAD16 (`1''`)  实测 521 Hz = C5

  也就是这台琴正好覆盖 **两个八度 C3~C5**，简谱的 `1` 在这里是 C3。
  早先按"1 = C4"算，整张频率表高了一个八度。
"""

from __future__ import annotations

import os

from . import layout
from .paths import app_dir

# 大调音阶的半音偏移
_SEMI = [0, 2, 4, 5, 7, 9, 11]
_BASE = 130.81278265        # C3


def pitch_freq(pitch: str) -> float:
    """简谱音名 -> 频率（Hz）。带撇号往上一个八度，带点往下。"""
    p = pitch
    octave = 0
    while p.endswith("'"):
        octave += 1
        p = p[:-1]
    if p.endswith('.'):
        octave -= 1
        p = p[:-1]
    if p == '8':
        return _BASE * 2.0
    try:
        d = int(p)
    except ValueError:
        d = 1
    d = max(1, min(7, d))
    return _BASE * (2.0 ** octave) * (2.0 ** (_SEMI[d - 1] / 12.0))


# 琴上 16 个音的理论频率
KEY_FREQS: list[tuple[str, float]] = [
    (p, pitch_freq(p)) for p in layout.all_pitches()
]


def sample_path(pitch: str) -> str:
    """音名 -> 采样文件（Windows 文件名不能有撇号，`'` 写成了 `_up`）。"""
    return os.path.join(app_dir(), 'assets', 'notes',
                        '%s.wav' % pitch.replace("'", '_up'))
