# -*- coding: utf-8 -*-
"""调试：拼接 '5 5 6 6'，看频谱通量曲线到底长什么样。"""

import os
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RATE = 48000
SPB = 0.5            # 120 BPM


def load_note(pitch):
    name = (pitch.replace("'", '_up').replace('.', '_low').replace('#', 's'))
    path = os.path.join(ROOT, 'assets', 'notes', name + '.wav')
    with wave.open(path, 'rb') as w:
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768.0
    return a


def build(seq):
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


seq = [('5', 1), ('5', 1), ('6', 1), ('6', 1), ('5', 2)]
a = build(seq)
print('序列: %s   总长 %.2f 秒' % (' '.join(p for p, _ in seq), len(a) / RATE))
print('音符起点应该在: 0.00  0.50  1.00  1.50  2.00 秒\n')

times, flux = flux_curve(a)
fm = flux.max()
print('flux 最大值 %.1f' % fm)
print('\n%-9s %12s %8s' % ('时间(s)', 'flux', '占比'))
print('-' * 32)
for t, f in zip(times, flux):
    if 1.6 <= t <= 2.6 or t <= 0.15 or (0.9 <= t <= 1.15):
        bar = '#' * int(f / fm * 40)
        print('%-9.3f %12.1f %7.1f%%  %s' % (t, f, f / fm * 100, bar))

# 局部峰值
print('\n所有局部峰（占比 > 5%）：')
for i in range(1, len(flux) - 1):
    if flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
        r = flux[i] / fm
        if r > 0.05:
            print('  t=%.3f  占比 %.1f%%' % (times[i], r * 100))
