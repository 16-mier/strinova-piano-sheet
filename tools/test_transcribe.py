# -*- coding: utf-8 -*-
"""离线验证「听音记谱」：用 16 个原声采样拼一段演奏，看能不能还原回谱子。

这样不用真去游戏里弹就能验证识别算法。

用法：python tools/test_transcribe.py
"""

from __future__ import annotations

import os
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import layout, transcribe            # noqa: E402

NOTES = os.path.join(ROOT, 'assets', 'notes')
RATE = 48000
BPM = 120
SPB = 60.0 / BPM          # 每拍秒数 = 0.5

# 测试旋律（音高, 几拍）
MELODY: list[tuple[str, float]] = [
    ('1', 1), ('1', 1), ('5', 1), ('5', 1),
    ('6', 1), ('6', 1), ('5', 2),
    ('4', 1), ('4', 1), ('3', 1), ('3', 1),
    ('2', 1), ('2', 1), ('1', 2),
    ("1'", 1), ("2'", 1), ("3'", 1), ("5'", 1),
]

# 用音高名 -> 文件名
def safe_name(pitch: str) -> str:
    return (pitch.replace("'", '_up')
                 .replace('.', '_low')
                 .replace('#', 's'))


_cache: dict[str, np.ndarray] = {}


def load_note(pitch: str) -> np.ndarray:
    if pitch in _cache:
        return _cache[pitch]
    path = os.path.join(NOTES, safe_name(pitch) + '.wav')
    with wave.open(path, 'rb') as w:
        ch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768.0
    if ch == 2:
        a = a[::2]
    if rate != RATE:                     # 简单重采样（线性）
        n = int(len(a) * RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n),
                      np.arange(len(a)), a)
    _cache[pitch] = a
    return a


def render_sequence(seq: list[tuple[str, float]]) -> np.ndarray:
    """把 [(音高, 几拍), ...] 拼成一段音频。

    每个音只占时长的 70%，剩下的留静音 —— 真实弹奏就是这样
    （采样会衰减，人也不会把两个音死死贴在一起）。
    """
    total = sum(b for _p, b in seq)
    out = np.zeros(int(total * SPB * RATE) + RATE)
    cursor = 0
    for pitch, beats in seq:
        dur = int(beats * SPB * RATE)
        if pitch and dur > 0:
            n = load_note(pitch)
            take = max(1, int(dur * 0.7))
            seg = n[:min(len(n), take)]
            if len(seg):
                # 尾巴做个淡出，避免硬切造成"咔"的宽带噪声
                fade = min(len(seg), int(0.02 * RATE))
                if fade > 1:
                    seg = seg.copy()
                    seg[-fade:] *= np.linspace(1.0, 0.0, fade)
                out[cursor:cursor + len(seg)] += seg
        cursor += dur
    peak = np.abs(out).max()
    if peak > 0:
        out = out / peak * 0.9
    return out


def main() -> int:
    print('输入旋律: %s' % ' '.join(p or '休' for p, _b in MELODY))

    audio = render_sequence(MELODY)
    print('合成音频: %.2f 秒, 采样率 %d' % (len(audio) / RATE, RATE))

    tokens, hits = transcribe.transcribe(audio, RATE, bpm=BPM, snap=0.25)
    print('\n识别到 %d 个音：' % len(hits))
    print('%-8s %-6s %8s %10s %10s %8s'
          % ('时间(s)', '音高', '拍位置', '实测频率', '理论频率', '偏差'))
    print('-' * 60)
    ok = 0
    for i, h in enumerate(hits):
        tf = dict(transcribe.KEY_FREQS).get(h.pitch, 0.0)
        mark = ''
        if i < len(MELODY):
            if h.pitch == MELODY[i][0]:
                mark = '  OK'
                ok += 1
            else:
                mark = '  <-- 期望 %s' % MELODY[i][0]
        print('%-8.3f %-6s %8.2f %10.1f %10.1f %+7.1f%s'
              % (h.time, h.pitch, h.beat, h.freq, tf, h.cents, mark))

    print('\n还原正确: %d / %d' % (ok, min(len(hits), len(MELODY))))
    print('\n生成的谱面文本:')
    text, _ = transcribe.to_sheet_text(audio, RATE, bpm=BPM, snap=0.25)
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
