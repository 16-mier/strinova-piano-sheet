# -*- coding: utf-8 -*-
"""专扫 CNMF 的 L1 稀疏强度 λ —— 治"一个音被反复上报"。

背景：CNMF 在"和弦/强干扰"上明显强过 NNLS，但"单音·慢"只有 0.35
（NNLS 0.969）。原因不是模板长度（试过加长，没用），而是
**乘法更新的解不唯一** —— 模板自带衰减形状，"一个持续的音"既可以由
一列解释、也可以由错开的几列叠出来，两种情况残差一样小。

分母上加常数 λ（等价于对 H 的 L1 惩罚）能把这个自由度收掉。

这个脚本只跑两个场景、扫 λ 的量级 —— 快，够定性。

    python tools/cnmf_sparse_sweep.py
    python tools/cnmf_sparse_sweep.py --n 6
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sd = _load('synth_dataset', os.path.join(ROOT, 'tools', 'synth_dataset.py'))
ev = _load('eval_detectors', os.path.join(ROOT, 'tools', 'eval_detectors.py'))
cp = _load('cnmf_probe', os.path.join(ROOT, 'tools', 'cnmf_probe.py'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=5)
    ap.add_argument('--iters', type=int, default=80)
    ap.add_argument('--scenes', default='单音·慢,和弦,强干扰')
    ap.add_argument('--rel', type=float, default=0.40)
    ap.add_argument('--gap', type=float, default=0.25)
    args = ap.parse_args()

    scenes = [s for s in args.scenes.split(',') if s]
    bank = sd.SampleBank()
    mel = sd.mel_matrix()
    W, keys = cp.build_cnmf_dict(bank, mel)
    clips = [c for c in ev.build_clips(args.n, 8.0, 20260701, bank)
             if c['scene'] in scenes]
    print('字典 W %s　测试 %d 段　场景 %s' % (W.shape, len(clips), scenes))

    # 先把 Y（线性 mel 谱）算好缓存 —— 扫 λ 时不用重算特征
    Ys = []
    for c in clips:
        F = sd.features_from_wave(c['wave'], mel, log=False)
        Ys.append(np.maximum(F[:, :, sd.CTX // 2].T.astype(np.float64), 0.0))

    # 看一眼 den 的量级，λ 要跟它比
    H0 = np.full((W.shape[1], Ys[0].shape[1]), 0.5)
    den = cp._conv_t(W, cp._conv(W, H0))
    print('den 量级：中位 %.4g　90%%位 %.4g　（λ 取这附近的量级才有效）'
          % (float(np.median(den)), float(np.percentile(den, 90))))
    print()

    lams = [0.3]
    rehits = [1.0, 1.2, 1.5, 2.0, 3.0, 5.0]
    hdr = '%-8s %-8s' % ('λ', 'rehit')
    for s in scenes:
        hdr += ' %18s' % s
    hdr += ' %18s' % '合计'
    print(hdr)
    print('-' * len(hdr))
    best = None
    for lam in lams:
        for rehit in rehits:
            line = '%-8.3g %-8.2f' % (lam, rehit)
            grand = [0, 0, 0]
            per = {}
            t0 = time.time()
            for sc in scenes:
                tot = [0, 0, 0]
                for c, Y in zip(clips, Ys):
                    if c['scene'] != sc:
                        continue
                    H = cp.cnmf(Y, W, iters=args.iters, lam=lam)
                    pred = cp.peaks_from_H(H, keys, cp.HOP / sd.SR,
                                           rel=args.rel, min_gap=args.gap,
                                           rehit=rehit)
                    tp, fp, fn, _w = ev.match(pred, c['truth'])
                    tot[0] += tp
                    tot[1] += fp
                    tot[2] += fn
                per[sc] = ev.prf(*tot)
                for i in range(3):
                    grand[i] += tot[i]
                line += ' %18s' % ('%.2f/%.2f/%.2f' % per[sc])
            P, R, F1 = ev.prf(*grand)
            mark = ''
            if best is None or F1 > best[0]:
                best = (F1, lam, rehit)
                mark = '  ←'
            line += ' %18s%s' % ('%.2f/%.2f/%.2f' % (P, R, F1), mark)
            line += '   %.1fs' % (time.time() - t0)
            print(line)
    print('-' * len(hdr))
    print('最好：合计 F1 %.3f @ λ=%.3g rehit=%.2f' % best)
    return 0


if __name__ == '__main__':
    sys.exit(main())
