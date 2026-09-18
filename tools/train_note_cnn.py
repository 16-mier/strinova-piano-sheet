# -*- coding: utf-8 -*-
"""训练一个**帧级 15 路多标签**的音符检测器（torch，离线）。

为什么是"帧级多标签"而不是"onset + 音高"
------------------------------------------
现有 NNLS 检测器最大的短板是**和弦召回率 0.32**（实测，见
`tools/eval_detectors.py`）—— 每次只敢报"涨得最猛的那一个键"，
因为它的判决逻辑是**逐个键比高低**（`rise_ratio` / `share_ratio` /
`max_per_onset` 三处都在做"选一个赢家"）。

而"这一帧哪些键在响"本来就是**多个独立的是/否问题**，
15 个 sigmoid 各管各的，天然不会有"只能报一个"的偏见。
这就是换模型的主要动机 —— 不是"深度模型更高级"，是**任务形式对上了**。

数据从哪来
----------
`tools/synth_dataset.py` 合成：这台琴每个键就是一段固定采样的回放，
所以任意演奏都能拼出来、**标注是完美的**。真实乐器做不到
（音色随力度连续变化，拿不到干净标签）。

标签把 `8` 并进 `1'`（两者采样逐样本相同，物理不可分）→ 15 类。

用法
----
    python tools/train_note_cnn.py --clips 400 --epochs 24
    python tools/train_note_cnn.py --clips 60 --epochs 3      # 冒烟测试
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

_spec = importlib.util.spec_from_file_location(
    'synth_dataset', os.path.join(ROOT, 'tools', 'synth_dataset.py'))
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

OUT_NPZ = os.path.join(ROOT, 'core', 'note_cnn.npz')
OUT_META = os.path.join(ROOT, 'core', 'note_cnn.json')


# ----------------------------------------------------------------------
# 模型
# ----------------------------------------------------------------------

def build_model(n_mel: int = 64, ctx: int = 5, n_out: int = 15):
    """小型 CNN —— 输入 (B, 1, n_mel, ctx)，输出 (B, n_out) 概率。

    ★ 尺寸是按 CPU 训练算过的 ★
      输入很小（64×5），所以两次 stride-2（沿频率轴）之后只剩 16×5；
      整套前向约 7×10^8 FLOPs，CPU 上 ~0.1 秒/batch，
      400 段数据 20 个 epoch 大约 20 分钟 —— 能在开发机上直接跑完，
      不用 GPU（这台机器也没有）。
    """
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(1, 16, 3, padding=1),
                nn.BatchNorm2d(16), nn.ReLU(inplace=True),
                nn.Conv2d(16, 32, 3, padding=1, stride=(2, 1)),
                nn.BatchNorm2d(32), nn.ReLU(inplace=True),
                nn.Conv2d(32, 32, 3, padding=1, stride=(2, 1)),
                nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            )
            # 自适应池化兜住"mel 维数以后可能改"这件事
            self.pool = nn.AdaptiveAvgPool2d((4, 1))
            self.head = nn.Sequential(
                nn.Flatten(),
                nn.Linear(32 * 4, 128), nn.ReLU(inplace=True),
                nn.Dropout(0.15),
                nn.Linear(128, n_out),
            )

        def forward(self, x):
            return self.head(self.pool(self.body(x)))

    return Net()


# ----------------------------------------------------------------------
# 训练
# ----------------------------------------------------------------------

def train(clips: int, epochs: int, dur: float, lr: float, batch: int,
          val_every: int, seed: int, threads: int, out_npz: str):
    import torch
    import torch.nn as nn

    torch.set_num_threads(max(1, threads))
    torch.manual_seed(seed)

    print('[1/4] 合成训练数据（%d 段 × %.0f 秒）…' % (clips, dur), flush=True)
    t0 = time.time()
    bank = sd.SampleBank()
    # ★ 训练和评估必须用**不同的 seed** ★
    #   评估集在 tools/eval_detectors.py 里用 seed=20260701 生成；
    #   这里用别的 seed，否则就是在自己见过的数据上打分。
    X, Y, keys = sd.build_dataset(n_clips=clips, dur=dur, seed=seed, bank=bank)
    print('      X %s  Y %s  正样本率 %.4f  用时 %.0fs'
          % (X.shape, Y.shape, float(Y.mean()), time.time() - t0), flush=True)
    if len(X) == 0:
        print('没有数据，退出'); return 1

    # 打乱 + 切验证集
    rng = np.random.default_rng(seed + 1)
    perm = rng.permutation(len(X))
    X, Y = X[perm], Y[perm]
    n_val = max(1, int(len(X) * 0.08))
    Xv, Yv = X[:n_val], Y[:n_val]
    Xt, Yt = X[n_val:], Y[n_val:]

    xt = torch.from_numpy(Xt[:, None, :, :])
    yt = torch.from_numpy(Yt)
    xv = torch.from_numpy(Xv[:, None, :, :])
    yv = torch.from_numpy(Yv)

    model = build_model(n_mel=X.shape[1], ctx=X.shape[2], n_out=Y.shape[1])
    n_param = sum(p.numel() for p in model.parameters())
    print('[2/4] 模型 %d 个参数　训练 %d 帧　验证 %d 帧'
          % (n_param, len(Xt), len(Xv)), flush=True)

    # ★ 正样本只占 ~19%，不加权的话模型会往"全 0"塌 ★
    pos_w = torch.tensor(
        (1.0 - Yt.mean(axis=0)) / np.maximum(Yt.mean(axis=0), 1e-6),
        dtype=torch.float32).clamp(1.0, 12.0)
    crit = nn.BCEWithLogitsLoss(pos_weight=pos_w)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    best = -1.0
    best_state = None
    n = len(Xt)
    print('[3/4] 开训…', flush=True)
    for ep in range(1, epochs + 1):
        model.train()
        idx = torch.randperm(n)
        tot = 0.0
        nb = 0
        for i in range(0, n, batch):
            b = idx[i:i + batch]
            opt.zero_grad()
            out = model(xt[b])
            loss = crit(out, yt[b])
            loss.backward()
            opt.step()
            tot += float(loss.detach())
            nb += 1
        sched.step()
        model.eval()
        with torch.no_grad():
            pv = torch.sigmoid(model(xv)).numpy()
        f1, pr, rc = _f1(pv, Yv)
        improved = f1 > best
        if improved:
            best = f1
            best_state = {k: v.detach().clone()
                          for k, v in model.state_dict().items()}
        if ep % val_every == 0 or ep == epochs or improved:
            print('  ep %2d/%d  loss %.4f  验证 P %.3f  R %.3f  F1 %.3f%s'
                  % (ep, epochs, tot / max(1, nb), pr, rc, f1,
                     '  ← best' if improved else ''), flush=True)

    if best_state is not None:
        model.load_state_dict(best_state)
    print('[4/4] 最好验证 F1 = %.4f' % best, flush=True)

    # ---- 导出成 numpy（运行时零依赖）----
    export_numpy(model, keys, n_mel=X.shape[1], ctx=X.shape[2], out=out_npz,
                 mel=sd.mel_matrix(n_mel=X.shape[1]))
    print('      权重已写到 %s' % out_npz, flush=True)
    return 0


def _f1(prob: np.ndarray, y: np.ndarray, thr: float = 0.5):
    pred = prob >= thr
    truth = y >= 0.5
    tp = float((pred & truth).sum())
    fp = float((pred & ~truth).sum())
    fn = float((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return f, p, r


def export_numpy(model, keys, n_mel: int, ctx: int, out: str,
                 mel: np.ndarray | None = None):
    """把 torch 权重拍平成 numpy 数组存 npz。

    ★ 为什么要导出而不是直接带 torch 跑 ★
      这是给普通用户打包用的工具，torch 是几百 MB。
      模型本身只有几百 KB 的矩阵乘法 —— 用 numpy 手写前向
      （`core/neural.py`）就够，推理侧一行新依赖都不用加。

    ★★ mel 矩阵必须一起存 ★★
      训练时的 mel 是 `librosa.filters.mel(norm='slaney')`，而推理端
      要是为了这点东西去依赖 librosa 就白折腾了。手写一份行不行？
      实测**不行**：librosa 的 Slaney mel 是**分段函数**
      （1000 Hz 以上走对数、以下走线性），手写连续公式的版本
      逐元素相对差到 **2.5 倍** —— 特征分布整体偏移，
      模型输出会变成垃圾而且**不报任何错**。
      所以直接把矩阵存下来，两边用同一份。
    """
    import torch
    sd_ = model.state_dict()
    arrs = {}
    for k, v in sd_.items():
        arrs[k] = v.detach().cpu().numpy().astype(np.float32)
    if mel is not None:
        arrs['mel'] = np.asarray(mel, dtype=np.float32)
    np.savez_compressed(out, **arrs)
    meta = dict(keys=list(keys), n_mel=int(n_mel), ctx=int(ctx),
                arch='cnn3-pool4x1',
                note='由 tools/train_note_cnn.py 生成，'
                     '推理见 core/neural.py（纯 numpy）')
    with open(os.path.splitext(out)[0] + '.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--clips', type=int, default=400)
    ap.add_argument('--epochs', type=int, default=24)
    ap.add_argument('--dur', type=float, default=7.0)
    ap.add_argument('--lr', type=float, default=2e-3)
    ap.add_argument('--batch', type=int, default=256)
    ap.add_argument('--val-every', type=int, default=2)
    ap.add_argument('--seed', type=int, default=1234)
    ap.add_argument('--threads', type=int, default=0)
    ap.add_argument('--out', default=OUT_NPZ)
    args = ap.parse_args()
    th = args.threads or max(1, (os.cpu_count() or 4) - 1)
    return train(args.clips, args.epochs, args.dur, args.lr, args.batch,
                 args.val_every, args.seed, th, args.out)


if __name__ == '__main__':
    sys.exit(main())
