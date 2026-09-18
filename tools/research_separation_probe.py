# -*- coding: utf-8 -*-
"""调研用实验：强干扰下「拒识判据」的对比 +「已知精确字典」的几个升级点验证。

**这个脚本不进主程序**，纯粹给方案选型提供实测数字。

关键设计：**检测问题，不是分类问题**
    正类帧 = 同一段干扰 + 琴声
    负类帧 = 同一段干扰（不加琴声）
两者唯一的差别就是「有没有琴声」。这才是「打谱时枪声不断、怎么不误报」
的真实问题。如果拿「干净琴声」当正类去比「纯干扰」，区分会过于容易，
AUC 全是 1.0，没有信息量。

回答四个问题：

  Q1  加了噪声字典之后，「残差 > 0.5 就拒识」还成立吗？
      → 对比不同噪声基数量 K 下，各判据的 AUC 怎么变。
  Q2  替代判据哪个最好？
      → 残差 / 目标能量占比 / 目标激活占比 / 匹配子空间检测统计量 /
        逐频点白化后的 MSD，五种判据一起算。
  Q3  干扰「泄漏」到 15 维目标子空间的能量有多少？最相近的单列是谁？
      → 这决定理论上限：宽带噪声泄漏低，同音域乐器泄漏高。
  Q4  两个便宜的改进有多大用？
      → (a) 统一模板与观测的窗长；(b) 非负 L1（FISTA + 软阈值）。

只依赖 numpy + soundfile（和主程序同一约束）。跑法：
    python tools/research_separation_probe.py
"""

from __future__ import annotations

import math
import os
import sys
import time

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

SR = 48000
WIN = 2880                 # 分析窗 60 ms
FFT_N = 4096
FMIN, FMAX = 90.0, 6000.0
TPL_AT, TPL_DUR = 0.012, 0.050
PGM_ITERS = 100
HOP = 960                  # 20 ms
TPLT = 2880                # 「统一窗长」变体里模板也取 60 ms
MAX_FRAMES = 400           # 每类最多取多少帧（控耗时）
SNRS = (0.0, -6.0, -12.0)

PITCHES = ['1', '2', '3', '4', '5', '6', '7', '8',
           "1'", "2'", "3'", "4'", "5'", "6'", "7'", "1''"]

SEP = '=' * 78
SUB = '-' * 78


# ---------------------------------------------------------------- 采样

def load_samples() -> dict[str, np.ndarray]:
    out = {}
    for p in PITCHES:
        path = os.path.join(ROOT, 'assets', 'notes',
                            p.replace("'", '_up') + '.wav')
        a, r = sf.read(path, dtype='float64', always_2d=False)
        if a.ndim > 1:
            a = a.mean(axis=1)
        assert r == SR, '采样率不是 48k: %s' % r
        m = float(np.abs(a).max())
        out[p] = a / m if m > 0 else a
    return out


def group_pitches(samples: dict[str, np.ndarray]) -> list[list[str]]:
    """逐样本比较去重（`8` 和 `1'` 波形相同 → 合并成一列）。"""
    groups: list[list[str]] = []
    for p in PITCHES:
        a = samples[p]
        for g in groups:
            b = samples[g[0]]
            if abs(len(a) - len(b)) > 16:
                continue
            n = min(len(a), len(b))
            if float(np.abs(a[:n] - b[:n]).max()) < 1e-6:
                g.append(p)
                break
        else:
            groups.append([p])
    return groups


# ---------------------------------------------------------------- 谱

_MASK_CACHE: dict[int, np.ndarray] = {}


def freq_mask(fft_n: int) -> np.ndarray:
    if fft_n not in _MASK_CACHE:
        f = np.fft.rfftfreq(fft_n, 1.0 / SR)
        _MASK_CACHE[fft_n] = (f >= FMIN) & (f <= FMAX)
    return _MASK_CACHE[fft_n]


def mag_spec(seg: np.ndarray, mode: str, fft_n: int = FFT_N) -> np.ndarray:
    """幅度谱。两种取法，用来对比「窗长是否统一」。

    legacy : 复刻现有实现 —— pad 到 fft_n，再乘 hanning(fft_n) 的前 len 点。
             于是 2400 样本的模板在窗值 0.93 处被硬截断，
             2880 样本的观测在窗值 0.64 处被硬截断 → 两者泄漏模式不同。
    fixed  : 窗长 = 信号长度（hanning(len)），不 pad。
    """
    y = np.asarray(seg, dtype=np.float64).reshape(-1)
    if mode == 'legacy':
        n = len(y)
        if n < fft_n:
            y = np.pad(y, (0, fft_n - n))
        elif n > fft_n:
            y = y[:fft_n]
        return np.abs(np.fft.rfft(y * np.hanning(fft_n)[:len(y)]))
    w = np.hanning(len(y))
    return np.abs(np.fft.rfft(y * w, n=fft_n))


def spec_mode(mode: str) -> str:
    return 'legacy' if mode == 'legacy' else 'fixed'


def build_A(samples, groups, mode: str) -> np.ndarray:
    cols = []
    for g in groups:
        a = samples[g[0]]
        seg = (a[int(TPL_AT * SR):int(TPL_AT * SR) + TPLT]
               if mode == 'matched'
               else a[int(TPL_AT * SR):int((TPL_AT + TPL_DUR) * SR)])
        cols.append(mag_spec(seg, spec_mode(mode)))
    A = np.stack(cols, axis=1)[freq_mask(FFT_N)]
    for k in range(A.shape[1]):
        n = float(np.linalg.norm(A[:, k]))
        if n > 0:
            A[:, k] /= n
    return A


# ---------------------------------------------------------------- 求解器

def orth(A: np.ndarray):
    U, s, _ = np.linalg.svd(A, full_matrices=False)
    r = int(np.sum(s > (s[0] * 1e-8 if len(s) else 0)))
    return U[:, :r], max(r, 1)


def pgm(AtA, Aty, n, iters=PGM_ITERS, L=None):
    x = np.zeros(n)
    if L is None:
        L = float(np.linalg.eigvalsh(AtA).max())
    step = 1.0 / max(L, 1e-12)
    for _ in range(iters):
        x = np.maximum(0.0, x - step * (AtA @ x - Aty))
    return x


def fista_nn(AtA, Aty, n, lam, iters=200, L=None):
    """min ½||y-Ax||² + λ||x||₁  s.t. x ≥ 0。

    复合近端算子：prox_{step·λ‖·‖₁ + ι_{x≥0}}(v) = max(0, v - step·λ)
    （v ≥ 0 时「先软阈值再截负」与它等价）。
    """
    if L is None:
        L = float(np.linalg.eigvalsh(AtA).max())
    step = 1.0 / max(L, 1e-12)
    t = step * lam
    x = np.zeros(n)
    z = x.copy()
    tk = 1.0
    for _ in range(iters):
        v = z - step * (AtA @ z - Aty)
        xn = np.maximum(0.0, v - t)
        tn = (1.0 + math.sqrt(1.0 + 4.0 * tk * tk)) * 0.5
        z = xn + ((tk - 1.0) / tn) * (xn - x)
        x, tk = xn, tn
    return x


def nmf_mu(V: np.ndarray, K: int, iters: int = 60, seed: int = 0) -> np.ndarray:
    """在幅度谱上跑 NMF（乘性更新）学噪声基 —— 模拟「从干扰里学 K 个基」。"""
    rng = np.random.default_rng(seed)
    F, M = V.shape
    avg = max(float(V.mean()), 1e-6)
    W = rng.random((F, K)) * avg
    H = rng.random((K, M)) * avg
    eps = 1e-9
    for _ in range(iters):
        H = H * ((W.T @ (V / (W @ H + eps))) / (W.sum(axis=0)[:, None] + eps))
        W = W * (((V / (W @ H + eps)) @ H.T) / (H.sum(axis=1)[None, :] + eps))
    for k in range(K):
        nn = float(np.linalg.norm(W[:, k]))
        if nn > 0:
            W[:, k] /= nn
    return W


# ---------------------------------------------------------------- 判据

class Ctx:
    """一帧要用的所有预计算量。"""

    def __init__(self, A: np.ndarray, W: np.ndarray | None, R: np.ndarray | None):
        self.A = A
        self.nA = A.shape[1]
        self.AtA = A.T @ A
        Q, r = orth(A)
        self.QA, self.rA = Q, r
        self.nF = A.shape[0]
        self.W = W
        if W is not None and W.shape[1] > 0:
            AW = np.hstack([A, W])
            self.AW = AW
            self.AtAW = AW.T @ AW
            self.nAW = AW.shape[1]
        else:
            self.AW = None
        if R is not None:
            self.inv = 1.0 / np.sqrt(R + 1e-9)
            Aw = A * self.inv[:, None]
            Qw, rw = orth(Aw)
            self.Qw, self.rw = Qw, rw
        else:
            self.inv = None

    KEYS = ('残差(纯A)', 'MSD(纯A)', 'MSD(逐频点白化)',
            '残差(联合A|W)', '目标能量占比', '目标激活占比', '目标重建占比')

    def scores(self, y: np.ndarray) -> dict[str, float]:
        """y = 幅度谱（已 mask、非负）。方向统一成「越大越像目标」。"""
        ny = float(np.linalg.norm(y))
        if ny <= 1e-12:
            return {}
        yn = y / ny
        out: dict[str, float] = {}

        x = pgm(self.AtA, self.A.T @ yn, self.nA)
        out['残差(纯A)'] = -float(np.linalg.norm(yn - self.A @ x))

        p = self.QA.T @ yn
        e_in = float(p @ p)
        e_out = max(1.0 - e_in, 1e-15)
        out['MSD(纯A)'] = (e_in / self.rA) / (e_out / max(self.nF - self.rA, 1))

        if self.AW is not None:
            z = pgm(self.AtAW, self.AW.T @ yn, self.nAW)
            x2, h2 = z[:self.nA], z[self.nA:]
            ax, wh = self.A @ x2, self.W @ h2
            ea, ew = float(ax @ ax), float(wh @ wh)
            out['残差(联合A|W)'] = -float(np.linalg.norm(yn - ax - wh))
            out['目标能量占比'] = ea / (ea + ew + 1e-15)
            out['目标激活占比'] = (float(x2.max()) /
                                   (float(x2.max()) + float(h2.max()) + 1e-15))
            out['目标重建占比'] = ea / float(yn @ yn)

        if self.inv is not None:
            yw = yn * self.inv
            p = self.Qw.T @ yw
            e_in = float(p @ p)
            e_out = max(float(yw @ yw) - e_in, 1e-15)
            out['MSD(逐频点白化)'] = ((e_in / self.rw) /
                                     (e_out / max(len(yw) - self.rw, 1)))
        return out


def auc(pos, neg) -> float:
    """Mann-Whitney AUC。pos（目标帧）分数应当更高。"""
    pos = np.asarray(pos, dtype=float)
    neg = np.asarray(neg, dtype=float)
    n1, n0 = len(pos), len(neg)
    if n1 == 0 or n0 == 0:
        return float('nan')
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind='mergesort')
    ranks = np.empty(len(allv), dtype=float)
    ranks[order] = np.arange(1, len(allv) + 1)
    sv = allv[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    r1 = float(ranks[:n1].sum())
    return (r1 - n1 * (n1 + 1) / 2.0) / (n1 * n0)


# ---------------------------------------------------------------- 信号

def synth(events, samples, dur) -> np.ndarray:
    a = np.zeros(int(dur * SR))
    for t, p, g in events:
        s = samples[p]
        i = int(t * SR)
        n = min(len(s), len(a) - i)
        if n > 0:
            a[i:i + n] += s[:n] * g
    return a


def gunshot(dur=0.08, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    env = np.exp(-np.arange(n) / (0.012 * SR))
    x = rng.standard_normal(n) * env
    y = np.zeros(n)
    acc = 0.0
    for i in range(n):
        acc = 0.55 * acc + 0.45 * x[i]
        y[i] = acc
    m = float(np.abs(y).max())
    return y / m if m > 0 else y


def gun_track(n, seed=7) -> np.ndarray:
    g = gunshot()
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    t = 0.05
    while t < n / SR - 0.2:
        i = int(t * SR)
        m = min(len(g), n - i)
        out[i:i + m] += g[:m] * 0.9
        t += float(rng.uniform(0.15, 0.35))
    return out


def harmonic_bgm(n, f0=196.0, seed=0, vib=4.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 26):
        if k * f0 > 0.45 * SR:
            break
        ph = float(rng.uniform(0, 2 * np.pi))
        mod = 1.0 + 0.15 * np.sin(2 * np.pi * vib * t + float(rng.uniform(0, 6)))
        out += (1.0 / k) * mod * np.sin(2 * np.pi * k * f0 * t + ph)
    m = float(np.abs(out).max())
    return out / m * 0.6 if m > 0 else out


def vocal(n, f0=233.0, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    f = f0 * (1.0 + 0.022 * np.sin(2 * np.pi * 5.2 * t))
    phase = 2 * np.pi * np.cumsum(f) / SR
    out = np.zeros(n)
    for k in range(1, 40):
        fk = k * f0
        if fk > 0.45 * SR:
            break
        env = (1.0 / k) * (1.0 + 3.0 * math.exp(-((fk - 700) / 300.0) ** 2)
                           + 2.0 * math.exp(-((fk - 1200) / 400.0) ** 2))
        out += env * np.sin(k * phase + float(rng.uniform(0, 6)))
    m = float(np.abs(out).max())
    return out / m * 0.6 if m > 0 else out


def shift_cents(x: np.ndarray, cents: float) -> np.ndarray:
    r = 2.0 ** (cents / 1200.0)
    n = max(int(len(x) / r), 8)
    idx = np.linspace(0.0, len(x) - 1.0, n)
    return np.interp(idx, np.arange(len(x)), x)


# ---------------------------------------------------------------- 帧采集

def frames_of(audio: np.ndarray, mode: str, min_rms: float = 0.015,
              cap: int = MAX_FRAMES):
    out = []
    pos = 0
    sm = spec_mode(mode)
    while pos + WIN <= len(audio):
        seg = audio[pos:pos + WIN]
        if float(np.sqrt(np.mean(seg * seg))) >= min_rms:
            out.append((mag_spec(seg, sm)[freq_mask(FFT_N)], pos / SR))
        pos += HOP
    if len(out) > cap:
        idx = np.linspace(0, len(out) - 1, cap).astype(int)
        out = [out[i] for i in idx]
    return out


def notes_track(samples, dur, gain=1.0):
    ev = [(0.35 + i * 0.62, p, gain) for i, p in enumerate(PITCHES)]
    return synth(ev, samples, dur), ev


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def leakage(specs, A, QA):
    """干扰落在 15 维子空间的比例 + 与最相近单列的最大余弦。"""
    ls, cs = [], []
    An = A / np.maximum(np.linalg.norm(A, axis=0, keepdims=True), 1e-12)
    for y in specs:
        n = float(np.linalg.norm(y))
        if n <= 1e-12:
            continue
        yn = y / n
        p = QA.T @ yn
        ls.append(float(p @ p))
        cs.append(float(np.max(An.T @ yn)))
    if not ls:
        return 0.0, 0.0
    return float(np.mean(ls)), float(np.mean(cs))


# ---------------------------------------------------------------- 主流程

def main() -> int:
    t_start = time.perf_counter()
    samples = load_samples()
    groups = group_pitches(samples)
    print(SEP)
    print('采样 %d 个 → 去重后 %d 列（合并：%s）'
          % (len(samples), len(groups),
             ', '.join('+'.join(g) for g in groups if len(g) > 1) or '无'))

    DUR = 7.6
    n = int(DUR * SR)
    rng = np.random.default_rng(11)

    guns = gun_track(n)
    white = rng.standard_normal(n) * 0.15
    bgm = harmonic_bgm(n, 196.0)
    voc = vocal(n, 233.0)
    op = synth([(0.30 + i * 0.55, "5'", 1.0) for i in range(13)], samples, DUR)
    op = shift_cents(op, 70.0)[:n]
    if len(op) < n:
        op = np.pad(op, (0, n - len(op)))
    mix = guns + bgm + white + 0.6 * voc

    interferences = [
        ('枪声', guns),
        ('白噪', white),
        ('稳态BGM', bgm),
        ('人声', voc),
        ('其它钢琴', op),
        ('混合', mix),
    ]

    for mode, tag in (
            ('legacy', '现有实现：模板 50 ms / 观测 60 ms，都 pad 到 4096 再乘 Hann'),
            ('matched', '统一窗长：模板也用 60 ms，窗长 = 信号长度')):
        print(SEP)
        print('【谱取法】%s' % tag)
        A = build_A(samples, groups, mode)
        QA, rA = orth(A)
        print('字典 A：%d 频点 × %d 列（秩 %d）' % (A.shape[0], A.shape[1], rA))

        # 干净琴声帧（只用来做泄漏对照）
        clean_au, _ev = notes_track(samples, DUR)
        clean_fr = [s for s, _t in frames_of(clean_au, mode)]

        neg_frames = {nm: [s for s, _t in frames_of(sig, mode)]
                      for nm, sig in interferences}

        # ---- Q3 泄漏 ----
        print(SUB)
        print('Q3  干扰谱落在「15 维目标子空间」的比例  ||P_A y||²/||y||²')
        print('    以及「与最相近的那个键的余弦」，余弦接近 1 就会直接误报该键')
        An = A / np.maximum(np.linalg.norm(A, axis=0, keepdims=True), 1e-12)

        def leak(frs):
            return leakage(frs, A, QA)

        l0, c0 = leak(clean_fr)
        print('    %-10s 子空间占比 %5.3f   最大余弦 %5.3f' % ('干净琴声', l0, c0))
        for nm, frs in neg_frames.items():
            l, c = leak(frs)
            flag = '★ 会误报' if c > 0.75 else ''
            print('    %-10s 子空间占比 %5.3f   最大余弦 %5.3f   %s'
                  % (nm, l, c, flag))

        # ---- 噪声基（oracle）----
        V = np.hstack([np.asarray(neg_frames[nm]).T for nm, _s in interferences])
        R = np.mean(np.hstack([np.asarray(neg_frames[k]).T
                               for k in ('枪声', '白噪', '稳态BGM')]) ** 2, axis=1)
        R = R + 1e-3 * float(R.mean()) + 1e-12
        print(SUB)
        print('噪声基用全部干扰帧（%d 帧）跑 NMF 学，oracle 上界；'
              '白化用枪声+白噪+BGM 的逐频点功率' % V.shape[1])

        # ---- Q1/Q2 ----
        print(SUB)
        print('Q1/Q2  检测 AUC：同段干扰「有琴声 vs 没琴声」')
        print('       （1.00 = 完美；0.50 = 完全瞎猜。正类=干扰+琴声，负类=纯干扰）')
        for snr in SNRS:
            print('  ┌─ SNR = %+.0f dB（琴声比干扰）' % snr)
            allres: dict[int, dict[str, dict[str, float]]] = {}
            for K in (0, 8, 16, 32):
                W = None if K == 0 else nmf_mu(V, K, seed=3)
                ctx = Ctx(A, W, R)
                res: dict[str, dict[str, float]] = {}
                for nm, sig in interferences:
                    nt, ev = notes_track(samples, DUR)
                    g = 10 ** (snr / 20.0) * rms(sig) / max(rms(nt), 1e-12)
                    pos_au = sig + nt * g
                    pos_all = frames_of(pos_au, mode)
                    posf = [s for t, _p, _g in ev
                            for s, ft in pos_all if t + 0.07 <= ft <= t + 0.30]
                    if not posf:
                        continue
                    ps = [ctx.scores(y) for y in posf]
                    qs = [ctx.scores(y) for y in neg_frames[nm]]
                    for key in ps[0]:
                        res.setdefault(key, {})[nm] = auc(
                            [p[key] for p in ps], [q[key] for q in qs])
                allres[K] = res
            names = [nm for nm, _s in interferences]
            keys = [k for k in Ctx.KEYS if allres[32].get(k)]
            print('  │  %-16s %s' % ('判据 \\ 噪声基K',
                                     '  '.join('%9s' % nm for nm in names)))
            for key in keys:
                for K in (0, 8, 16, 32):
                    row = allres[K].get(key, {})
                    print('  │  %-14s K=%-2d %s'
                          % (key, K, '  '.join('%9.3f' % row.get(nm, float('nan'))
                                               for nm in names)))
            print('  └─')

        # ---- Q4b 非负 L1 ----
        print(SUB)
        print('Q4b  非负 L1（FISTA + 软阈值）对「1+3+5 和弦」虚假激活的影响')
        ctx0 = Ctx(A, None, None)
        au = synth([(1.0, '1', 1.0), (1.0, '3', 1.0), (1.0, '5', 1.0)],
                   samples, 2.2)
        frame = None
        for s, ft in frames_of(au, mode, min_rms=0.02):
            if 1.10 <= ft <= 1.32:
                frame = s
                break
        if frame is not None:
            yn = frame / float(np.linalg.norm(frame))
            Aty = A.T @ yn
            L = float(np.linalg.eigvalsh(ctx0.AtA).max())
            x0 = pgm(ctx0.AtA, Aty, A.shape[1], L=L)
            o = np.argsort(-x0)
            print('     λ=0（NNLS）   前 5 强：%s'
                  % ' '.join('%s %.3f' % (groups[k][-1], x0[k]) for k in o[:5]))
            for lam in (0.005, 0.01, 0.02, 0.05, 0.10):
                x = fista_nn(ctx0.AtA, Aty, A.shape[1], lam, L=L)
                o = np.argsort(-x)
                print('     λ=%-6.3f     前 5 强：%s'
                      % (lam, ' '.join('%s %.3f' % (groups[k][-1], x[k])
                                       for k in o[:5])))

        # ---- 计时 ----
        ctx_full = Ctx(A, nmf_mu(V, 16, seed=3), R)
        y0 = clean_fr[0]
        for _k in range(20):
            ctx_full.scores(y0)
        t0 = time.perf_counter()
        for _k in range(200):
            ctx_full.scores(y0)
        dt = (time.perf_counter() - t0) / 200 * 1000
        print(SUB)
        print('一帧跑完全部判据：%.3f ms（hop 10 ms → 占用 %.1f%%）'
              % (dt, dt / 10.0 * 100))

    print(SEP)
    print('总耗时 %.1f s' % (time.perf_counter() - t_start))
    print("""
读法：
  * 「残差(纯A)」是现基线用的判据。看它和「残差(联合A|W)」在 K 增大时的差别
    —— 那就是「加了噪声字典之后残差拒识失效」的实测证据。
  * 「其它钢琴」「稳态BGM」两列是所有判据的照妖镜：说明同音域乐器的物理上限。
  * SNR 从 0 → -12 dB 看判据还能不能撑住。
""")
    return 0


if __name__ == '__main__':
    sys.exit(main())
