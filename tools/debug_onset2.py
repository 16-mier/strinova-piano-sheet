# -*- coding: utf-8 -*-
"""分析 onset 检测：期望的位置 vs 实际检测到的位置。"""

from __future__ import annotations

import os
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.transcribe import detect_onsets      # noqa: E402

RATE = 48000
SPB = 0.5          # 120 BPM


def load_note(pitch: str) -> np.ndarray:
    name = (pitch.replace("'", '_up').replace('.', '_low').replace('#', 's'))
    with wave.open(os.path.join(ROOT, 'assets', 'notes', name + '.wav'),
                   'rb') as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768.0


def build(seq) -> np.ndarray:
    out = np.zeros(int(sum(b for _p, b in seq) * SPB * RATE) + RATE)
    cur = 0
    for pitch, beats in seq:
        dur = int(beats * SPB * RATE)
        if pitch:
            n = load_note(pitch)
            take = int(dur * 0.7)
            seg = n[:min(len(n), take)].copy()
            fade = min(len(seg), int(0.02 * RATE))
            if fade > 1:
                seg[-fade:] *= np.linspace(1.0, 0.0, fade)
            out[cur:cur + len(seg)] += seg
        cur += dur
    peak = np.abs(out).max()
    return out / peak * 0.9 if peak else out


def flux_curve(a, hop=256, win=1024):
    a = a / (np.abs(a).max() or 1.0)
    n = max(1, (len(a) - win) // hop + 1)
    W = np.hanning(win)
    specs = np.empty((n, win // 2 + 1))
    for i in range(n):
        specs[i] = np.abs(np.fft.rfft(a[i * hop:i * hop + win] * W))
    d = np.diff(specs, axis=0)
    flux = np.sum(np.maximum(d, 0.0), axis=1)
    times = (np.arange(len(flux)) * hop + win // 2) / RATE
    return times, flux


def main() -> int:
    seq = [('5', 1), ('5', 1), ('6', 1), ('6', 1), ('5', 2)]
    a = build(seq)
    expect = [0.0, 0.5, 1.0, 1.5, 2.0]

    got = [round(x / RATE, 3) for x in detect_onsets(a, RATE)]
    print('期望 onset : %s' % expect)
    print('检测到 onset: %s' % got)
    print()

    t, f = flux_curve(a)
    fm = f.max()
    print('大峰（局部峰且 >45%%）：')
    for i in range(1, len(f) - 1):
        if f[i] >= f[i - 1] and f[i] >= f[i + 1] and f[i] / fm > 0.45:
            print('   t=%.3f   %.1f%%' % (t[i], f[i] / fm * 100))

    for lo, hi in ((1.40, 1.62), (1.90, 2.12)):
        print('\n--- %.2f ~ %.2f 秒 ---' % (lo, hi))
        for ti, fi in zip(t, f):
            if lo <= ti <= hi:
                print('   %.3f   %5.1f%%  %s'
                      % (ti, fi / fm * 100, '#' * int(fi / fm * 40)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
