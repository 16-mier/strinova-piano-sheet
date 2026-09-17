# -*- coding: utf-8 -*-
"""听音记谱 —— 把在游戏里弹的琴声，还原成谱面文本。

为什么可行
----------
游戏里那台琴就 16 个固定音色，而我们手上有这 16 个原始采样。
所以问题不是"识别任意乐器"，而是"在这 16 个已知答案里挑一个"，难度低得多。

流程
----
    1. 找每个音的**起点**：短时能量的上升沿（onset detection）
    2. 在每个起点后面取一小段，估**基频**（谐波乘积谱 HPS）
    3. 把基频匹配到琴上最近的那个音（顺便报告偏差多少音分）
    4. 按 BPM 把相邻起点的时间差量化成拍数，生成记谱 token

和弦（同时按多个键）目前会识别成"一个音"，因为 HPS 只会给出最强的那条基频。
真要拆和弦得做多基频估计，那是另一个量级的工作，先不做。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import encode, layout

# 和琴键对应的理论频率（C4 大调，中音 1 = C4）
_SEMI = [0, 2, 4, 5, 7, 9, 11]
_BASE = 261.6255653


def pitch_freq(pitch: str) -> float:
    p = pitch
    octave = 0
    while p.endswith("'"):
        octave += 1
        p = p[:-1]
    if p.endswith('.'):
        octave -= 1
        p = p[:-1]
    if p == '8':
        return _BASE * 2.0
    try:
        d = int(p)
    except ValueError:
        d = 1
    d = max(1, min(7, d))
    return _BASE * (2.0 ** octave) * (2.0 ** (_SEMI[d - 1] / 12.0))


# 琴上 16 个音的理论频率
KEY_FREQS: list[tuple[str, float]] = [
    (p, pitch_freq(p)) for p in layout.all_pitches()
]


@dataclass
class NoteHit:
    """识别出来的一个音。"""

    time: float          # 秒
    pitch: str           # 匹配到的键
    freq: float          # 实测基频
    cents: float         # 跟理论值的偏差（音分）
    beat: float = 0.0    # 量化后的拍位置
    beats: float = 1.0   # 量化后的时长（拍）


# ---------------------------------------------------------------- onset

def detect_onsets(audio: np.ndarray, rate: int,
                  hop: int = 256, win: int = 1024,
                  thresh_ratio: float = 0.45,
                  min_gap_s: float = 0.08,
                  local_win_s: float = 0.35,
                  floor_db: float = -60.0) -> list[int]:
    """找音符起点，返回样本下标列表。

    用**频谱通量**（spectral flux）而不是纯能量 —— 因为两个不同音高连在一起时，
    总能量可能几乎不变，但频谱已经翻了天。

    ★ 阈值为什么要定这么高（45%）★
      实测下来，真正的音符起点会让通量跳到 80~100%（占全局最大值的比例），
      而同一个采样衰减过程中的起伏只有 5~45%。两者之间有明显鸿沟，
      所以把阈值放在鸿沟里，既能抓住弱起的音，又不会被衰减抖动骗到。
      阈值定低了（比如 5%）会把一次弹奏切成十几个"音符"。
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    a = np.asarray(audio, dtype=np.float64)
    if len(a) < win:
        return []

    peak_amp = float(np.abs(a).max())
    if peak_amp <= 1e-6:
        return []
    # 归一化，免得音量大小影响阈值
    a = a / peak_amp

    n_frames = max(1, (len(a) - win) // hop + 1)
    if n_frames < 3:
        return []

    window = np.hanning(win)
    specs = np.empty((n_frames, win // 2 + 1), dtype=np.float64)
    for i in range(n_frames):
        seg = a[i * hop: i * hop + win] * window
        specs[i] = np.abs(np.fft.rfft(seg))

    # 频谱通量：只累计"变大"的部分
    diff = np.diff(specs, axis=0)
    flux = np.sum(np.maximum(diff, 0.0), axis=1)

    fmax = float(flux.max())
    if fmax <= 0:
        return []

    # ★ 用「局部归一化」而不是全局最大值当基准 ★
    #   如果拿全局最大峰当 100%，万一某个音弹得特别响，其他音就全被压到阈值以下了。
    #   改成每个候选峰跟它**自己邻域**的最大峰比，这样每个音都被公平对待。
    #   邻域取 ±0.25 秒：够小，不会被隔壁响音压制；够大，能盖住采样自身的衰减抖动。
    win_frames = max(3, int(local_win_s * rate / hop))
    cands: list[tuple[float, int]] = []
    for i in range(1, len(flux) - 1):
        v = flux[i]
        if v < flux[i - 1] or v < flux[i + 1]:       # 要局部峰
            continue
        lo = max(0, i - win_frames)
        hi = min(len(flux), i + win_frames + 1)
        local_max = float(flux[lo:hi].max())
        if local_max <= 0:
            continue
        if v / local_max < thresh_ratio:
            continue
        cands.append((v, i))

    min_gap = int(min_gap_s * rate)
    cands.sort(key=lambda t: -t[0])
    picked: list[int] = []
    for _v, i in cands:
        pos = int(i * hop + win // 2)
        if any(abs(pos - p) < min_gap for p in picked):
            continue
        picked.append(pos)

    onsets = sorted(picked)

    # 开头的第一个音（起点太靠近 0 时，flux 峰落在窗内测不出来）
    head = a[:win]
    if not onsets or onsets[0] > int(0.25 * rate):
        if float(np.sqrt(np.mean(head ** 2))) > 0.02:
            onsets.insert(0, win // 4)

    return onsets


# ---------------------------------------------------------------- 基频

def f0_hps(seg: np.ndarray, rate: int,
           fmin: float = 200.0, fmax: float = 1300.0) -> float:
    """谐波乘积谱估基频。"""
    if seg.ndim > 1:
        seg = seg.mean(axis=1)
    x = np.asarray(seg, dtype=np.float64)
    if len(x) < 256:
        return 0.0
    x = x - x.mean()
    if not np.any(x):
        return 0.0

    m = len(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(m)))
    if spec.max() <= 0:
        return 0.0

    hps = spec.copy()
    for h in range(2, 6):
        dec = spec[::h]
        hps[:len(dec)] *= dec

    freqs = np.fft.rfftfreq(m, 1.0 / rate)
    lo = int(np.searchsorted(freqs, fmin))
    hi = int(np.searchsorted(freqs, fmax))
    if hi <= lo:
        return 0.0
    k = lo + int(np.argmax(hps[lo:hi]))

    if 0 < k < len(hps) - 1:
        y0, y1, y2 = hps[k - 1], hps[k], hps[k + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            return float(freqs[k]) + 0.5 * (y0 - y2) / den * (
                freqs[1] - freqs[0])
    return float(freqs[k])


def nearest_pitch(freq: float) -> tuple[str, float]:
    """把频率匹配到琴上最近的键，返回 (音高, 偏差音分)。"""
    if freq <= 0:
        return ('', 0.0)
    best_p, best_cents = KEY_FREQS[0][0], 1e9
    for p, f in KEY_FREQS:
        cents = 1200.0 * math.log2(freq / f)
        if abs(cents) < abs(best_cents):
            best_p, best_cents = p, cents
    return (best_p, best_cents)


# ---------------------------------------------------------------- 主流程

def transcribe(audio: np.ndarray, rate: int,
               bpm: int = 120,
               snap: float = 0.25,
               win_s: float = 0.10,
               max_cents: float = 60.0) -> tuple[list[str], list[NoteHit]]:
    """把音频转成 (token 列表, 识别详情)。

    参数
        bpm    用来把秒换算成拍
        snap   量化精度（拍），0.25 = 十六分音符
        win_s  每个音取多长来分析音高
        max_cents  偏差超过这么多音分就当作"不是琴声"丢掉
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    a = np.asarray(audio, dtype=np.float64)
    if len(a) == 0 or rate <= 0:
        return ([], [])

    onsets = detect_onsets(a, rate)
    if not onsets:
        return ([], [])

    spb = 60.0 / max(1, bpm)
    win = max(256, int(win_s * rate))

    hits: list[NoteHit] = []
    for pos in onsets:
        seg = a[pos:pos + win]
        f0 = f0_hps(seg, rate)
        pitch, cents = nearest_pitch(f0)
        if not pitch or abs(cents) > max_cents:
            continue
        hits.append(NoteHit(time=pos / rate, pitch=pitch,
                            freq=f0, cents=cents))

    if not hits:
        return ([], [])

    t0 = hits[0].time
    for h in hits:
        h.beat = (h.time - t0) / spb

    # 时长 = 到下一个音的距离；最后一个默认 1 拍
    for i, h in enumerate(hits):
        if i + 1 < len(hits):
            gap = hits[i + 1].beat - h.beat
        else:
            gap = 1.0
        q = round(gap / snap) * snap
        h.beats = max(snap, q)

    tokens: list[str] = []
    for h in hits:
        b = round(h.beat / snap) * snap
        tokens.append(encode.token_for(h.beats, h.pitch))

    # 开头的空档补休止符
    lead = round(hits[0].beat / snap) * snap
    if lead > 1e-6:
        tokens = encode.split_gap(lead) + tokens

    return (tokens, hits)


def to_sheet_text(audio: np.ndarray, rate: int, bpm: int = 120,
                  snap: float = 0.25, title: str = '听音记谱') -> tuple[str, list[NoteHit]]:
    """直接生成可以贴进编辑器的谱面文本。"""
    tokens, hits = transcribe(audio, rate, bpm=bpm, snap=snap)
    if not tokens:
        return ('', [])
    body = ' '.join(tokens)
    lines = [body[i:i + 200] for i in range(0, len(body), 200)]
    text = '%s\n#%d#\n%s' % (title, bpm, '\n'.join(lines))
    return (text, hits)
