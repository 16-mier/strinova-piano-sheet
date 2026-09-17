# -*- coding: utf-8 -*-
"""合成 16 个琴键的音色。

为什么不用游戏原声：那个打谱器把采样打包进了 Godot 私有格式（`.sample`），
文件目录还是加密的，逆向不值得。这里改用「基频 + 若干泛音 + 指数衰减包络」
合成，听感接近钢琴/马林巴，用来听音高和节奏完全够。

频率取 C4 大调：中音 1 = C4 (261.63Hz)，往上按自然音阶排。
"""

from __future__ import annotations

import math
import os
import struct
import wave

from . import layout

RATE = 44100
DUR = 1.1                 # 单音持续时长（秒）

_BASE = 261.6255653       # C4
# 大调音阶的半音偏移（do re mi fa sol la si）
_SEMITONES = [0, 2, 4, 5, 7, 9, 11]
# 泛音配比 —— 决定音色
_PARTIALS = [(1, 1.0), (2, 0.45), (3, 0.22), (4, 0.12), (5, 0.07), (6, 0.04)]


def pitch_freq(pitch: str) -> float:
    """音高标签 -> 频率(Hz)。琴上没有的音也会算出一个值。"""
    p = pitch or '1'
    sharp = p.startswith('#')
    p = p.lstrip('#')

    octave = 0
    while p.endswith("'"):
        octave += 1
        p = p[:-1]
    if p.endswith('.'):
        octave -= 1
        p = p[:-1]

    if p == '8':
        # 游戏里的第 8 个键（乐理上等于高音 1）
        semi = 0
        octave += 1
    else:
        try:
            d = int(p)
        except ValueError:
            d = 1
        d = max(1, min(7, d))
        semi = _SEMITONES[d - 1]

    if sharp:
        semi += 1

    return _BASE * (2.0 ** octave) * (2.0 ** (semi / 12.0))


def render(freq: float, dur: float = DUR, rate: int = RATE) -> bytes:
    """合成一个音，返回 16-bit 单声道 PCM。"""
    n = int(dur * rate)
    nyq = rate / 2.0
    buf = bytearray()
    for i in range(n):
        t = i / rate
        # 快起音 + 指数衰减，避免爆音
        env = math.exp(-3.2 * t) * (1.0 - math.exp(-300.0 * t))
        s = 0.0
        for k, amp in _PARTIALS:
            f = freq * k
            if f >= nyq:
                break
            s += amp * math.sin(2.0 * math.pi * f * t)
        v = int(max(-1.0, min(1.0, s * env * 0.22)) * 32767)
        buf += struct.pack('<h', v)
    return bytes(buf)


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
    import sys
    d = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'assets', 'notes')
    files = ensure_notes(d, force=True)
    for pitch, path in files.items():
        print('%-5s %8.2f Hz  %s' % (pitch, pitch_freq(pitch),
                                     os.path.basename(path)))
    print('\n共 %d 个 -> %s' % (len(files), d))
