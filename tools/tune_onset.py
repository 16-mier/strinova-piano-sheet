# -*- coding: utf-8 -*-
"""参数扫描：找出 onset 检测最好的一组参数。

评分方式：把识别出的音高序列跟输入的旋律逐个比对（只看音高，不看时长）。
"""

from __future__ import annotations

import itertools
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import transcribe                 # noqa: E402
from tools.test_transcribe import (MELODY, RATE, render_sequence)  # noqa: E402


def score(tokens: list[str], expect: list[str]) -> float:
    """把 token 里的音高剥出来，跟期望序列比 —— 返回 F1。"""
    got = []
    for tok in tokens:
        body = tok
        for ch in ('^', '-', '~'):
            body = body.replace(ch, '')
        if body and not body.startswith('#'):
            got.append(body)
    exp = [p for p, _b in expect]
    if not got or not exp:
        return 0.0
    # 逐位比对（简单版）
    hit = sum(1 for a, b in zip(got, exp) if a == b)
    prec = hit / len(got)
    rec = hit / len(exp)
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def main() -> int:
    audio = render_sequence(MELODY)
    print('测试旋律 %d 个音，合成音频 %.1f 秒\n'
          % (len(MELODY), len(audio) / RATE))

    results = []
    for ratio, gap, lwin in itertools.product(
            (0.25, 0.35, 0.45, 0.55, 0.65),
            (0.08, 0.10, 0.13),
            (0.10, 0.15, 0.20, 0.25, 0.35)):
        orig = transcribe.detect_onsets
        try:
            onsets = orig(audio, RATE, thresh_ratio=ratio,
                          min_gap_s=gap, local_win_s=lwin)
        except Exception:
            continue
        spb = 60.0 / 120
        hits = []
        for pos in onsets:
            seg = audio[pos:pos + int(0.10 * RATE)]
            f0 = transcribe.f0_hps(seg, RATE)
            p, c = transcribe.nearest_pitch(f0)
            if p and abs(c) <= 60:
                hits.append(p)
        f1 = score(hits, MELODY)
        results.append((f1, len(hits), ratio, gap, lwin))

    results.sort(key=lambda r: (-r[0], abs(r[1] - len(MELODY))))
    print('%-6s %6s %7s %7s %8s' % ('F1', '识别数', '阈值比', '最小间隔', '局部窗'))
    print('-' * 42)
    for f1, n, ratio, gap, lwin in results[:15]:
        print('%-6.3f %6d %7.2f %7.2f %8.2f' % (f1, n, ratio, gap, lwin))

    best = results[0] if results else None
    if best:
        print('\n最佳: 阈值比=%.2f 最小间隔=%.2f 局部窗=%.2f  (F1=%.3f, 识别 %d 个, 期望 %d 个)'
              % (best[2], best[3], best[4], best[0], best[1], len(MELODY)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
