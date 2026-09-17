# -*- coding: utf-8 -*-
"""实时跟弹的自检 —— 不用麦克风，直接喂合成音频块给检测器。

用法：python tools/test_live.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import numpy as np                                   # noqa: E402

from core import transcribe                          # noqa: E402
from core.live import LiveDetector                   # noqa: E402

RATE = 48000
BLOCK = 1024

_NOTE_CACHE: dict = {}


def load_note(pitch: str, rate: int = RATE):
    """读取游戏原始采样（比合成正弦真实得多 —— 合成音的谱会抖出假峰）。"""
    if pitch in _NOTE_CACHE:
        return _NOTE_CACHE[pitch]
    from core import audio_io, synth
    notes = synth.ensure_notes(os.path.join(ROOT, 'assets', 'notes'))
    path = notes.get(pitch)
    if not path or not os.path.isfile(path):
        return None
    a, sr = audio_io.load_audio(path, target_sr=rate)
    _NOTE_CACHE[pitch] = a
    return a


def render(seq, spb=0.5, amp=0.85):
    """把 [('1', 拍数), ...] 渲染成波形。"""
    total = int(sum(b for _p, b in seq) * spb * RATE) + RATE // 4
    out = np.zeros(total, dtype=np.float64)
    cur = 0
    for pitch, beats in seq:
        one = load_note(pitch)
        n = int(beats * spb * RATE * 0.8)
        if one is None or len(one) == 0:
            print('  (没有 %s 的采样，跳过)' % pitch)
        else:
            m = min(n, len(one))
            out[cur:cur + m] += one[:m] * amp
        cur += int(beats * spb * RATE)
    return out


def main() -> int:
    seq = [('1', 1.0), ('3', 1.0), ('5', 1.0), ('6', 1.0),
           ('1', 0.5), ('1', 0.5), ('5', 1.0)]
    wave = render(seq)
    print('测试序列：%s' % ' '.join(p for p, _b in seq))
    print('总长 %.1f 秒' % (len(wave) / RATE))

    det = LiveDetector(rate=RATE)
    got = []
    n_blocks = (len(wave) + BLOCK - 1) // BLOCK
    for i in range(n_blocks):
        chunk = wave[i * BLOCK:(i + 1) * BLOCK]
        if len(chunk) == 0:
            break
        for t, pitch, freq in det.push(chunk):
            got.append((t, pitch))
            print('  +%.3fs  %-4s  %7.1f Hz'
                  % (t, pitch, freq))

    print('-' * 56)
    want = [p for p, _b in seq]
    have = [p for _t, p in got]
    print('期望 %d 个：%s' % (len(want), ' '.join(want)))
    print('认出 %d 个：%s' % (len(have), ' '.join(have)))

    if '--trace' in sys.argv:
        i = sys.argv.index('--trace')
        a, b = 0.0, 1e9
        if i + 2 < len(sys.argv):
            a, b = float(sys.argv[i + 1]), float(sys.argv[i + 2])
        print('-' * 56)
        print('通量曲线 %.2f ~ %.2f 秒：' % (a, b))
        mx = max((f for _t, f, _p in det.log), default=1.0) or 1.0
        for t, f, pk in det.log:
            if a <= t <= b:
                bar = '#' * int(40 * f / mx)
                print('  %6.3fs  %9.1f  %-40s %s'
                      % (t, f, bar, 'PEAK' if pk else ''))
        print('-' * 56)
        print('内部状态：')
        print('  已喂样本 %d（%.3f 秒）' % (det._total, det._total / RATE))
        print('  _raw 窗口起点 %.3f 秒，长度 %d'
              % (det._abs_pos / RATE, len(det._raw)))
        print('  待定候选 %d 个：%s'
              % (len(det._cands), ['%.3f' % c[2] for c in det._cands[:8]]))
        print('  等音高窗口 %d 个：%s'
              % (len(det._waiting), ['%.3f' % (x / RATE)
                                     for x in det._waiting[:8]]))
        print('  上次定案 %.3f 秒' % det._last_settled_t)
        print('  已报音高：%s'
              % {k: round(v, 3) for k, v in det._last_pitch_t.items()})
        print('  被丢的 onset（%d 条）：' % len(det.rejects))
        for tt, why in det.rejects:
            print('    %6.3fs  %s' % (tt, why))

    ok = True
    if not have:
        print('[FAIL] 一个都没认出来')
        ok = False
    else:
        # 认出来的必须是期望序列的子序列（顺序不能乱、不能认错）
        it = iter(have)
        expect_order = all(any(p == w for p in it) for w in want)
        # 换个写法：双指针判断子序列
        j = 0
        for p in have:
            while j < len(want) and want[j] != p:
                j += 1
            if j >= len(want):
                expect_order = False
                break
            j += 1
        if not expect_order:
            print('[FAIL] 认出来的音不是期望序列的子序列')
            ok = False
        elif len(have) < len(want):
            print('[WARN] 少了 %d 个（实时检测漏音，可接受但要留意）'
                  % (len(want) - len(have)))
        else:
            print('[PASS] 全都认出来了')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
