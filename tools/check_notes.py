# -*- coding: utf-8 -*-
"""核对 assets/notes 里 16 个采样：文件名标的音高，和实际音高对不对得上。

用法：python tools/check_notes.py
"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import numpy as np                                   # noqa: E402

from core import audio_io, synth, transcribe as T    # noqa: E402

RATE = 48000


def peak_base(seg, rate, n_peaks: int = 6):
    """用「幅度谱前几个峰的间隔」估基频。

    这是最不容易出八度错的办法：谐波永远是基频的整数倍，
    所以**相邻峰之间的距离**就是基频本身 —— 不管基频那根柱子响不响。
    """
    m = len(seg)
    spec = np.abs(np.fft.rfft(seg * np.hanning(m)))
    if spec.max() <= 0:
        return 0.0, []
    freqs = np.fft.rfftfreq(m, 1.0 / rate)
    thr = spec.max() * 0.08
    idx = [i for i in range(1, len(spec) - 1)
           if spec[i] >= spec[i - 1] and spec[i] >= spec[i + 1]
           and spec[i] > thr]
    idx.sort(key=lambda i: -spec[i])
    top = sorted(float(freqs[i]) for i in idx[:n_peaks])
    if len(top) < 2:
        return (top[0] if top else 0.0), top
    diffs = [top[i + 1] - top[i] for i in range(len(top) - 1)]
    diffs = [d for d in diffs if d > 20.0]
    if not diffs:
        return top[0], top
    return float(min(diffs)), top


def main() -> int:
    notes = synth.ensure_notes(os.path.join(ROOT, 'assets', 'notes'))

    if '--windows' in sys.argv:
        print('扫分析窗口长度（越长的窗口频率分辨率越高，但会吃掉挨得近的音）')
        print('-' * 78)
        for wn in (4800, 7200, 9600, 12000, 14400, 19200, 24000):
            bad = []
            for pitch, path in sorted(notes.items(),
                                      key=lambda kv: T.pitch_freq(kv[0])):
                a, _sr = audio_io.load_audio(path, target_sr=RATE)
                if len(a) < wn:
                    a = np.pad(a, (0, wn - len(a)))
                got = T.match_key(a[:wn], RATE)[0]
                if not (got == pitch or {got, pitch} == {'8', "1'"}):
                    bad.append('%s->%s' % (pitch, got))
            print('窗口 %5d 样本 (%.2f 秒)  认错 %2d 个   %s'
                  % (wn, wn / RATE, len(bad), ' '.join(bad)))
        print('-' * 78)
        return 0

    if '--scores' in sys.argv:
        print('不同时间窗下，各键的匹配得分（只挑几个容易混的看）')
        print('-' * 76)
        for pitch in ['1', '8', "1'", "2'", '3', "3'", '5', "5'", '6', "6'"]:
            path = notes.get(pitch)
            if not path:
                continue
            a, _sr = audio_io.load_audio(path, target_sr=RATE)
            for start, wn in ((0, 12000), (4800, 12000), (0, 28800)):
                seg = a[start:start + wn]
                if len(seg) < wn:
                    seg = np.pad(seg, (0, wn - len(seg)))
                sc = T.key_scores(seg, RATE)
                top = '  '.join('%s=%+.2f' % (p, s) for p, s, _f in sc[:3])
                print('  %-4s [%5d..%5d]  %s'
                      % (pitch, start, start + wn, top))
            print()
        return 0

    if '--short' in sys.argv:
        print('短窗口下的识别（模拟弹得快时只能取 84ms）—— 看低频键分不分得开')
        print('-' * 78)
        for wn_ms in (30, 45, 60, 84, 120):
            wn = int(RATE * wn_ms / 1000)
            bad = []
            detail = []
            for pitch, path in sorted(notes.items(),
                                      key=lambda kv: T.pitch_freq(kv[0])):
                a, _sr = audio_io.load_audio(path, target_sr=RATE)
                if len(a) < wn:
                    a = np.pad(a, (0, wn - len(a)))
                seg = a[:wn]
                got, freq, conf = T.match_key(seg, RATE)
                f0, _db = T.estimate_f0_peak_ex(seg, RATE)
                ok = (got == pitch or {got, pitch} == {'8', "1'"})
                if not ok:
                    bad.append('%s->%s' % (pitch, got))
                detail.append('%s:%s(%.0fHz)' % (pitch, got or '?', f0))
            print('窗口 %3dms  认错 %2d 个：%s' % (wn_ms, len(bad),
                                                  ' '.join(bad)))
            print('        %s' % '  '.join(detail))
        print('-' * 78)
        return 0

    print('共 %d 个采样' % len(notes))
    print('%-6s %9s %9s   %s' % ('键', 'HPS', '理论', '幅度谱前 6 个峰 (Hz)'))
    print('-' * 92)
    bad = 0
    for pitch, path in sorted(notes.items(),
                              key=lambda kv: T.pitch_freq(kv[0])):
        a, sr = audio_io.load_audio(path, target_sr=RATE)
        if len(a) < 8192:
            a = np.pad(a, (0, 8192 - len(a)))
        seg = a[:8192]
        f_hps = T.f0_hps(seg, RATE)
        _f_pk, tops = peak_base(seg, RATE, n_peaks=6)
        th = T.pitch_freq(pitch)
        got, _f, margin = T.match_key(seg, RATE)
        ok = (got == pitch) or {got, pitch} == {'8', "1'"}
        if not ok:
            bad += 1
        print('%-6s %9.1f %9.1f   %s'
              % (pitch, f_hps, th,
                 '  '.join('%7.1f' % f for f in tops)))
    print('-' * 92)
    print('%s' % ('全部对得上 ✓' if bad == 0 else '有 %d 个对不上' % bad))
    return 0


if __name__ == '__main__':
    sys.exit(main())
