# -*- coding: utf-8 -*-
"""合成 16 个琴键的音色。

为什么不用游戏原声：那个打谱器把采样打包进了 Godot 私有格式（`.sample`），
文件目录还是加密的，逆向不值得。这里改用「基频 + 若干泛音 + 指数衰减包络」
合成，听感接近钢琴/马林巴，用来听音高和节奏完全够。

★ 音高基准**只认 `core/notes.py` 那一套** ★
  简谱的 `1` 在这台琴上是 **C3 (130.81 Hz)** —— 频率表的唯一真源是
  `notes.pitch_freq`，本模块把它**转出**，不再自己写一份。
  以前这里按「中音 1 = C4 (261.63Hz)」算，和真源**差一个八度**：
  `render.load_sample` 走到合成兜底时音高全是错的（整张表高了一个八度）。
  （那张表早先住在 `core/transcribe.py`（听音记谱）里，听音删掉之后
     挪到了 `core/notes.py` —— 它本来就不是听音，是"键的身份表"。）
"""

from __future__ import annotations

import os
import wave

import numpy as np

from . import layout
# ★ 转出别名 ★ —— 唯一真源在 `notes`；这里只是让老调用点
#   （`core/render.py`、`tools/synth_dataset.py`）拿到同一套频率。
from .notes import pitch_freq             # noqa: F401

RATE = 44100
DUR = 1.1                 # 单音持续时长（秒）

# 泛音配比 —— 决定音色
_PARTIALS = [(1, 1.0), (2, 0.45), (3, 0.22), (4, 0.12), (5, 0.07), (6, 0.04)]


def render(freq: float, dur: float = DUR, rate: int = RATE) -> bytes:
    """合成一个音，返回 16-bit 单声道 PCM。

    ★ numpy 向量化 ★
      以前是逐样本的 Python 循环（1.1 秒 × 44100 样本 × 6 个泛音 ≈ 29 万次
      `math.sin`，还要每次 `struct.pack` 追加 bytes）—— 单音要 100 ms 上下，
      而 `ui/keypad.py` 在 UI 线程里一次要生成 16 个 ⇒ 窗口白屏 1 秒多。
      现在每个泛音一次性相加、一次性转成 `<i2`，结果字节完全一致
      （浮点转整数的截断方向和原来的 `int()` 一样，都是向零取整）。
    """
    n = int(dur * rate)
    if n <= 0:
        return b''
    nyq = rate / 2.0
    t = np.arange(n, dtype=np.float64) / float(rate)
    # 快起音 + 指数衰减，避免爆音
    env = np.exp(-3.2 * t) * (1.0 - np.exp(-300.0 * t))
    s = np.zeros(n, dtype=np.float64)
    for k, amp in _PARTIALS:
        f = freq * k
        if f >= nyq:
            break
        s += amp * np.sin(2.0 * np.pi * f * t)
    v = np.clip(s * env * 0.22, -1.0, 1.0)
    return (v * 32767.0).astype('<i2').tobytes()


def write_wav(path: str, pcm: bytes, rate: int = RATE) -> None:
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def safe_name(pitch: str) -> str:
    """音高 -> 合法文件名。"""
    return (pitch.replace("'", '_up')
                 .replace('.', '_low')
                 .replace('#', 's'))


def ensure_notes(out_dir: str, force: bool = False) -> dict[str, str]:
    """保证 16 个键都有 wav，返回 {音高: wav路径}。"""
    os.makedirs(out_dir, exist_ok=True)
    result: dict[str, str] = {}
    for row in layout.PAD_GRID:
        for pitch in row:
            path = os.path.join(out_dir, '%s.wav' % safe_name(pitch))
            if force or not os.path.isfile(path):
                write_wav(path, render(pitch_freq(pitch)))
            result[pitch] = path
    return result


if __name__ == '__main__':
    d = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'assets', 'notes')
    files = ensure_notes(d, force=True)
    for pitch, path in files.items():
        print('%-5s %8.2f Hz  %s' % (pitch, pitch_freq(pitch),
                                     os.path.basename(path)))
    print('\n共 %d 个 -> %s' % (len(files), d))
