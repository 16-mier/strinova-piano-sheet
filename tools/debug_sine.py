# -*- coding: utf-8 -*-
"""排查：纯正弦为什么认不出来（合成音测试失败时用）。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

import numpy as np                                   # noqa: E402

from core import transcribe as T                     # noqa: E402

RATE = 48000


def main() -> int:
    for pitch in ('1', '3', '5'):
        f = T.pitch_freq(pitch)
        n = int(0.5 * RATE * 0.7)
        t = np.arange(n) / RATE
        a = np.sin(2 * np.pi * f * t) * np.exp(-4.0 * t) * 0.8
        seg = a[:4800] if len(a) >= 4800 else np.pad(
            a, (0, 4800 - len(a)))
        print('--- 合成 %s (%.2f Hz) ---' % (pitch, f))
        print('  f0_peak    = %.2f' % T.estimate_f0_peak(seg, RATE))
        print('  f0_hps     = %.2f' % T.f0_hps(seg, RATE))
        print('  match_key  = %s' % (T.match_key(seg, RATE),))
        print('  scores     = %s'
              % [(p, round(s, 2)) for p, s, _x in T.key_scores(seg, RATE)[:4]])
        print('  onsets     = %s' % T.detect_onsets(a, RATE)[:5])

    seq = ['1', '3', '5']
    spb = 0.5
    total = int(len(seq) * spb * RATE) + RATE
    audio = np.zeros(total, dtype=np.float64)
    for i, p in enumerate(seq):
        f = T.pitch_freq(p)
        n = int(spb * RATE * 0.7)
        t = np.arange(n) / RATE
        audio[i * int(spb * RATE):][:n] += (
            np.sin(2 * np.pi * f * t) * np.exp(-4.0 * t) * 0.8)
    info: dict = {}
    _tok, hits = T.transcribe(audio, RATE, bpm=120, min_margin=0.0,
                              info=info)
    print('--- 整段「1 3 5」---')
    print('  info   = %s' % {k: (round(v, 2) if isinstance(v, float) else v)
                             for k, v in info.items() if k != 'margin_all'})
    print('  hits   = %s' % [(round(h.time, 3), h.pitch, round(h.cents, 1))
                             for h in hits])
    return 0


if __name__ == '__main__':
    sys.exit(main())
