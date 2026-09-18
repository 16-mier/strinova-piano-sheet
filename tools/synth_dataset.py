# -*- coding: utf-8 -*-
"""合成「16 键琴」的训练数据 —— 标注问题一次性解决。

★ 为什么可以合成 ★
    这台琴不是真实乐器：每个键就是**一段固定采样的回放**。
    所以只要拿到那 16 段采样（`assets/notes/*.wav`），
    任意"演奏"都能拼出来，而且**每一帧该亮哪些键是我们自己定的** ——
    标注是完美的、免费的、要多少有多少。
    真实乐器做不到这一点，它们的音色随力度/触键连续变化。

★ 合成里必须有的"坏东西" ★
    只在干净信号上训出来的模型，一进游戏就废。所以每一条都要随机叠加：
      · 白噪（信噪比 5~30 dB 随机）
      · **音高不在琴上的干扰音**（模拟 BGM / 语音 / 音效里的乐音成分）
      · 低频轰鸣（枪声 / 爆炸的低频能量）
      · 简单混响（指数衰减的稀疏冲激响应，模拟房间/耳机泄漏）
      · 力度随机（0.15~1.0）—— 游戏里没人用同一力度弹

★ 标签怎么定 ★
    「这一帧这个键**在响**」（能量高于阈值就算），不是"这一帧按下了"。
    于是后处理只要找 0→1 的跳变就是 onset —— 一整条流水线里
    只剩下**一个**阈值，比现在那一堆手工门限（rise_norm / rise_floor /
    min_ratio / adapt_gate / share_ratio / tail_ratio …）干净得多。

用法（作为库）：
    from tools.synth_dataset import build_dataset
    X, Y, meta = build_dataset(seed=0, n_clips=200)
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import layout                                  # noqa: E402

# ----------------------------------------------------------------------
# 基本参数 —— 和分析端保持一致
# ----------------------------------------------------------------------

SR = 48000
WIN = 2880               # 分析窗 60 ms（和现有识别器一致）
CTX = 5                  # 上下文帧数（奇数）：特征是 ±2 帧 = ±40 ms
HOP_FEAT = 480           # 特征 hop 10 ms

# 标签的时间分辨率（和 HOP_FEAT 一致）
HOP_LABEL = HOP_FEAT

# 「在响」的能量门限 —— 相对满力度单音的峰值能量
#   -46 dB 大约对应采样的自然衰减尾巴末端，再低就是房间噪底了
ACTIVE_DB = -46.0

# ★ 不可分对 ★
#   `8` 和 `1'` 的采样**逐样本完全相同**（tools/cmp_twin.py 实测：
#   归一化后最大差 0.000000）。让模型去区分它俩是自欺欺人，
#   所以这两键**合成一类**，共 15 类；推理时两个键一起亮
#   （现有代码的 `views.twins()` 本来就是这么做的）。
ALIAS = {"8": "1'"}      # 后者代表这一类


def class_keys() -> list[str]:
    """15 个可区分的类别（`8` 并入 `1'`）。"""
    out = []
    for p in layout.all_pitches():
        if p in ALIAS:
            continue
        out.append(p)
    return out


def _freq_of(pitch: str) -> float:
    from core.synth import pitch_freq
    return pitch_freq(pitch)


# ----------------------------------------------------------------------
# 采样加载
# ----------------------------------------------------------------------

class SampleBank:
    """16 个键的采样 + 每个采样自己的"活跃包络"。

    活跃包络是**逐帧能量**（10 ms 一格，峰值归一），标签就是拿它比门限
    得来的 —— 这样"一个音的尾巴还在响"和"它已经不响了"由采样本身的
    物理衰减决定，不用我拍脑袋定时长。
    """

    def __init__(self, notes_dir: str | None = None, target_sr: int = SR):
        import soundfile as sf
        from core.paths import app_dir

        d = notes_dir or os.path.join(app_dir(), 'assets', 'notes')
        self.sr = target_sr
        self.wave: dict[str, np.ndarray] = {}
        self.env: dict[str, np.ndarray] = {}
        missing = []
        for p in layout.all_pitches():
            f = os.path.join(d, '%s.wav' % p.replace("'", '_up'))
            if not os.path.isfile(f):
                missing.append(p)
                continue
            x, sr = sf.read(f, dtype='float64', always_2d=False)
            if getattr(x, 'ndim', 1) > 1:
                x = x.mean(axis=1)
            if sr != target_sr:
                n = max(1, int(round(len(x) * target_sr / float(sr))))
                x = np.interp(np.linspace(0, len(x) - 1, n),
                              np.arange(len(x)), x)
                sr = target_sr
            x = np.asarray(x, dtype=np.float64)
            self.wave[p] = x
            self.env[p] = self._envelope(x)
        self.missing = missing
        # 峰值归一用的基准：满力度单音的最大帧能量
        self.ref = max((float(e.max()) for e in self.env.values()), default=1.0)

    @staticmethod
    def _envelope(x: np.ndarray, hop: int = HOP_LABEL) -> np.ndarray:
        """逐帧 RMS（10 ms 一格）。"""
        n = max(1, len(x) // hop)
        y = x[:n * hop].reshape(n, hop)
        return np.sqrt((y * y).mean(axis=1))

    @property
    def keys(self) -> list[str]:
        return list(self.wave)


# ----------------------------------------------------------------------
# 干扰信号
# ----------------------------------------------------------------------

def _fft_convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """FFT 卷积（numpy 自带，不引 scipy）。

    ★ 为什么自己写 ★
      1. `np.convolve(dry, h)` 是**直接卷积**：dry 288000 点 × h 上万点
         = 10^10 量级乘法，一段 6 秒音频要跑几十秒。
      2. `scipy.signal.fftconvolve` 是对的，但 `import scipy.signal`
         本身要 **2.2 秒**（实测）—— 而这段代码要在数据生成循环里
         被调上千次，第一次导入的代价不该算在头上；而且合成脚本
         最好是零额外依赖的。
    """
    n = len(x) + len(h) - 1
    nfft = 1 << max(1, (n - 1).bit_length())
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft),
                        nfft)[:n]


def _make_reverb(rng: np.random.Generator, sr: int = SR,
                 t60: float = 0.35, n_taps: int = 24) -> np.ndarray:
    """稀疏指数衰减冲激响应 —— 便宜的"房间感"。"""
    n = max(8, int(t60 * sr))
    h = np.zeros(n)
    idx = rng.integers(0, n, size=n_taps)
    amp = np.exp(-3.0 * idx / float(n)) * rng.uniform(0.3, 1.0, size=n_taps)
    h[idx] += amp
    h[0] += 1.0
    return h / (np.abs(h).sum() + 1e-9)


def _make_jammer(rng: np.random.Generator, dur: float,
                 sr: int = SR) -> np.ndarray:
    """「不是这台琴」的音乐干扰 —— 模拟 BGM / 语音 / 音效里的乐音。

    做法：拿一堆**半音阶**上的频率（故意包含琴上没有的音）做主音，
    每个音加前几个谐波、各自随机起止时间，再整体过一个慢包络。
    这比白噪难对付得多：白噪只在底噪上抬一点，而这个会**真的占住
    某些琴键的谐波位置**，逼模型去用"音色/起音形状"而不是"这根谐波有没有"。
    """
    n = int(dur * sr)
    out = np.zeros(n)
    t = np.arange(n) / sr
    # 12 平均律，从 C3 到 C6，随机挑几个
    base = 130.81278265
    semis = rng.choice(np.arange(0, 37), size=int(rng.integers(3, 9)),
                       replace=False)
    for s in semis:
        f0 = base * (2.0 ** (s / 12.0))
        if f0 > 2000:
            continue
        a = rng.uniform(0.02, 0.12)
        # 起止（可能会盖住整段）
        t0 = rng.uniform(0.0, max(0.0, dur - 0.5))
        t1 = min(dur, t0 + rng.uniform(0.3, 2.5))
        i0, i1 = int(t0 * sr), int(t1 * sr)
        if i1 <= i0:
            continue
        seg = t[i0:i1] - t[i0]
        env = np.exp(-2.0 * seg) * (1 - np.exp(-60 * seg))
        w = np.zeros_like(seg)
        for k, g in ((1, 1.0), (2, 0.4), (3, 0.18), (4, 0.08)):
            if f0 * k < sr / 2:
                w += g * np.sin(2 * np.pi * f0 * k * seg
                                + rng.uniform(0, 6.28))
        out[i0:i1] += a * env * w
    # 低频轰鸣（枪声/爆炸）
    if rng.random() < 0.5:
        n_low = int(rng.uniform(0.05, 0.25) * sr)
        i0 = int(rng.uniform(0, max(0.0, dur - 0.3)) * sr)
        n_low = min(n_low, max(0, n - i0))
        if n_low > 8:
            seg = np.arange(n_low) / sr
            boom = (np.sin(2 * np.pi * rng.uniform(45, 110) * seg)
                    * np.exp(-9.0 * seg))
            out[i0:i0 + n_low] += rng.uniform(0.05, 0.25) * boom
    return out


# ----------------------------------------------------------------------
# 造一条"演奏"
# ----------------------------------------------------------------------

# 音符之间的间隔（秒）—— 从"飞快连奏"到"慢歌"都覆盖
GAPS = np.array([0.09, 0.11, 0.14, 0.18, 0.24, 0.32, 0.45, 0.65, 0.95, 1.4])
# 同时按几个键（偏向单音，但和弦要够多，否则模型学不会）
CHORD_P = np.array([0.52, 0.26, 0.13, 0.09])      # 1 / 2 / 3 / 4 个键


def synth_clip(bank: SampleBank, rng: np.random.Generator,
               dur: float = 8.0, keys: list[str] | None = None,
               profile: dict | None = None):
    """合成一段演奏。返回 (波形, 标签矩阵 (K, T), 事件表)。

    `profile` 可以覆写场景参数（评测时要按场景分层）：
        chord_p   同时按几个键的概率（长度 4 的数组）
        gaps      音符间隔的候选值（秒）
        jam_snr   音乐干扰的信噪比范围（dB，越大越干净）
        noise_snr 白噪信噪比范围
        onsets    只在这些时刻起音（None = 随机；给固定序列可以造可复现的难例）
    """
    prof = profile or {}
    chord_p = np.asarray(prof.get('chord_p', CHORD_P), dtype=np.float64)
    chord_p = chord_p / chord_p.sum()
    gaps = np.asarray(prof.get('gaps', GAPS), dtype=np.float64)
    jam_lo, jam_hi = prof.get('jam_snr', (3.0, 25.0))
    n_lo, n_hi = prof.get('noise_snr', (10.0, 40.0))
    fixed_onsets = prof.get('onsets')

    keys = keys or class_keys()
    n = int(dur * SR)
    dry = np.zeros(n, dtype=np.float64)
    per_key = {k: np.zeros(n, dtype=np.float64) for k in keys}

    # ★★ 必须有一部分"完全没弹"的片段 ★★
    #   第一版训练集里几乎**每一帧**都有音在响（每秒 1~10 个音、
    #   每个音的标签活跃 600 ms，正样本率 16%），但**没有一段是安静的**。
    #   结果模型从来没见过"没人在弹"长什么样 —— 它在真实场景里
    #   对着静音和纯 BGM 也往外报音符：
    #   实测事件级精度只有 **0.227**（召回 0.748，误报是命中的三倍多）。
    #
    #   提高阈值治不了这个（只是把召回一起压下去），因为缺的是
    #   **负样本**，不是判决门限。所以这里让约 22% 的片段一个音都不弹。
    quiet = bool(rng.random() < 0.22)

    events = []
    if fixed_onsets is not None:
        plan = list(fixed_onsets)
    else:
        plan = None

    t = rng.uniform(0.05, 0.35)
    idx = 0
    while not quiet and t < dur - 0.6:
        if plan is not None:
            if idx >= len(plan):
                break
            t = float(plan[idx])
            if t >= dur - 0.6:
                break
        nk = int(rng.choice(len(chord_p), p=chord_p)) + 1
        nk = min(nk, len(keys))
        ks = rng.choice(keys, size=nk, replace=False)
        gain = float(rng.uniform(0.15, 1.0) ** 0.8)   # 偏向中高力度
        off = int(t * SR)
        for k in ks:
            s = bank.wave[k] * gain
            end = min(n, off + len(s))
            if end > off:
                per_key[k][off:end] += s[:end - off]
        events.append((t, sorted(ks), gain))
        idx += 1
        if plan is not None:
            continue
        t += float(rng.choice(gaps))

    for k in keys:
        dry += per_key[k]

    # ---- 混响 ----
    # ★ 必须用 FFT 卷积 ★
    #   一开始写的是 `np.convolve(dry, h)`：dry 有几十万个点、
    #   h 有上万点，那是 10^10 量级的乘法 —— 实测**一段 6 秒的音频
    #   要跑 39 秒**，根本造不出训练集。
    if rng.random() < 0.7:
        h = _make_reverb(rng, SR, t60=float(rng.uniform(0.15, 0.5)))
        wet = _fft_convolve(dry, h)[:n]
        mix = dry * 0.75 + wet * 0.75
    else:
        mix = dry

    # ---- 干扰 ----
    jam = _make_jammer(rng, dur)
    # 干扰的相对强度：让"琴声/干扰"落在给定区间
    clean_rms = float(np.sqrt((mix ** 2).mean())) + 1e-12
    jam_rms = float(np.sqrt((jam ** 2).mean())) + 1e-12
    snr_db = float(rng.uniform(jam_lo, jam_hi))
    jam = jam * (clean_rms / jam_rms) * (10 ** (-snr_db / 20.0))
    # ---- 白噪 ----
    noise = rng.normal(0, 1, n)
    nsnr_db = float(rng.uniform(n_lo, n_hi))
    noise = noise * (clean_rms / (float(np.sqrt((noise ** 2).mean())) + 1e-12)
                     ) * (10 ** (-nsnr_db / 20.0))
    sig = mix + jam + noise

    # 整条随机增益（模拟音量大小），再夹一下防削顶
    sig = sig * rng.uniform(0.35, 1.0)
    peak = float(np.abs(sig).max())
    if peak > 0.98:
        sig = sig * (0.98 / peak)

    # ---- 标签 ----
    frames = n // HOP_LABEL
    Y = np.zeros((len(keys), frames), dtype=np.float32)
    thr = bank.ref * (10 ** (ACTIVE_DB / 20.0))
    for i, k in enumerate(keys):
        env = bank._envelope(per_key[k])
        # `per_key` 里没有额外的增益（gain 已经乘进去了）——
        # 但 envelope 是按**原始采样**的比例算的，所以这里直接用
        m = min(frames, len(env))
        Y[i, :m] = (env[:m] > thr).astype(np.float32)
    return sig.astype(np.float32), Y, events


# ----------------------------------------------------------------------
# 特征
# ----------------------------------------------------------------------

def mel_matrix(n_fft: int = 4096, n_mel: int = 64, sr: int = SR,
               fmin: float = 90.0, fmax: float = 8000.0) -> np.ndarray:
    """mel 滤波器组矩阵 (n_mel, n_fft//2+1)。

    ★ 为什么要把它存下来 ★
      训练时用 librosa 生成，推理时只要 `mel @ |FFT|` 一次矩阵乘法 ——
      不用为了一组滤波器把 librosa 拖进运行时依赖。
    """
    import librosa
    m = librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=n_mel,
                            fmin=fmin, fmax=fmax, norm='slaney')
    return m.astype(np.float32)


def features_from_wave(x: np.ndarray, mel: np.ndarray, *,
                       win: int = WIN, hop: int = HOP_FEAT,
                       n_fft: int = 4096, ctx: int = CTX,
                       log_eps: float = 1e-4,
                       log: bool = True) -> np.ndarray:
    """波形 -> (T, n_mel, ctx) 特征。

    每个时间点取以它为中心的 `ctx` 帧 mel 谱，拼起来 ——
    模型因此能看到 ±20 ms 的上下文，这对"区分起音和衰减尾巴"是必需的。

    `log=False` 时输出**线性** mel（非负）—— 那是给 NMF/CNMF 那条路用的：
    非负矩阵分解的加法模型要求输入非负，log 压缩会把它破坏掉。

    用**滑窗视图 + 批量 rfft**，不逐帧调 `np.fft.rfft`（那样慢十倍）。
    """
    n = len(x)
    if n < win:
        x = np.pad(x, (0, win - n))
        n = win
    frames = 1 + (n - win) // hop
    # 滑窗视图：(frames, win)
    idx = np.arange(win)[None, :] + hop * np.arange(frames)[:, None]
    seg = x[idx]
    w = np.hanning(win).astype(np.float64)
    spec = np.abs(np.fft.rfft(seg * w, n=n_fft, axis=1))     # (frames, F)
    m = spec @ mel.T                                         # (frames, n_mel)
    if log:
        m = np.log10(m + log_eps)
    m = m.astype(np.float32)
    # 上下文拼接
    half = ctx // 2
    pad = np.pad(m, ((half, half), (0, 0)), mode='edge')
    out = np.empty((frames, m.shape[1], ctx), dtype=np.float32)
    for c in range(ctx):
        out[:, :, c] = pad[c:c + frames]
    return out


# ----------------------------------------------------------------------
# 数据集
# ----------------------------------------------------------------------

def build_dataset(n_clips: int = 240, dur: float = 8.0, seed: int = 0,
                  bank: SampleBank | None = None):
    """造一批 (特征, 标签)。返回 (X, Y, keys)。

    X: (N, n_mel, ctx) float32
    Y: (N, K)          float32
    """
    bank = bank or SampleBank()
    rng = np.random.default_rng(seed)
    mel = mel_matrix()
    keys = class_keys()

    Xs, Ys = [], []
    feat_hop_frames = CTX // 2          # 特征只输出"中心对齐"的那些帧
    for i in range(n_clips):
        sig, Y, _ev = synth_clip(bank, rng, dur=dur)
        F = features_from_wave(sig, mel)
        # F 的第 j 行对应波形时间 j*hop；标签 Y 的第 j 列对应同一时刻
        # （features_from_wave 的窗是 [j*hop, j*hop+win)，指标是窗尾，
        #   所以对齐到 j*hop + win。见下面的 shift 说明。）
        shift = WIN // HOP_LABEL        # 窗尾对齐 → 往后挪 60ms 的帧数
        m = min(len(F), Y.shape[1] - shift)
        if m <= 0:
            continue
        Xs.append(F[:m])
        Ys.append(Y[:, shift:shift + m].T)
    if not Xs:
        return (np.zeros((0, 64, CTX), np.float32),
                np.zeros((0, len(keys)), np.float32), keys)
    X = np.concatenate(Xs, axis=0)
    Y = np.concatenate(Ys, axis=0)
    return X, Y, keys


if __name__ == '__main__':
    import time
    t0 = time.time()
    bank = SampleBank()
    print('采样：%d 个键，缺 %s' % (len(bank.keys), bank.missing or '无'))
    print('类别（把 8 并进 1\'）：%s' % class_keys())
    X, Y, keys = build_dataset(n_clips=4, dur=6.0, seed=1, bank=bank)
    print('X %s  Y %s  正样本率 %.4f  用时 %.1fs'
          % (X.shape, Y.shape, float(Y.mean()), time.time() - t0))
