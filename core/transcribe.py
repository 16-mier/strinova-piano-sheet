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

# 和琴键对应的理论频率（大调音阶，中音 1 = C4）
#
# ★ 基准音为什么是 C3 而不是通常的 C4 ★
#   从 16 个游戏原始采样里量出来的（tools/check_notes.py）：
#       PAD1 (`1`)  实测 130 Hz = C3
#       PAD8 (`8`)  实测 259 Hz = C4
#       PAD9 (`1'`) 实测 259 Hz = C4   ← 和 `8` 同音，游戏里是两个键
#       PAD16(`1''`) 实测 521 Hz = C5
#   也就是这台琴正好覆盖 **两个八度 C3~C5**，简谱的 `1` 在这里是 C3。
#   之前按"1 = C4"算，整张频率表高了一个八度，识别时高音区全乱套。
_SEMI = [0, 2, 4, 5, 7, 9, 11]
_BASE = 130.81278265        # C3

# 识别出来的音符，本体最多占这么多拍（超出的时间写成休止符）
MAX_NOTE_BEATS = 1.0

# 置信度门槛的默认值（单位 **dB**，见 estimate_f0_peak_ex）。
# 实测：干净的游戏采样 22~26 dB；背景音乐 / 噪声做出来的"假峰"低得多。
# 卡在 8 附近：能滤掉伴奏，又不会把弱奏的琴音扔掉。
DEFAULT_MIN_MARGIN = 8.0


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

# 唯一音高表 —— 同音高的键只留一个代表（PAD 序号最小的那个）。
# 游戏里 `8` 和 `1'` 是同一个音高的两个键，识别上天然分不开；
# 与其每次随机挑一个，不如固定输出 `8`，这样谱面至少是稳定的。
UNIQUE_KEYS: list[tuple[str, float]] = []
for _p, _f in KEY_FREQS:
    if any(abs(1200.0 * math.log2(_f / _g)) < 50.0
           for _q, _g in UNIQUE_KEYS):
        continue
    UNIQUE_KEYS.append((_p, _f))


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


def nearest_pitch(freq: float, offset_cents: float = 0.0) -> tuple[str, float]:
    """把频率匹配到琴上最近的键，返回 (音高, 偏差音分)。

    offset_cents = 已知的整体跑调量（正数 = 实测偏高）。
    扣掉它之后再比，返回的也是扣掉之后的残差 —— 这样"整体升了 60 音分"
    的录音不会被硬塞到隔壁键上去。
    """
    if freq <= 0:
        return ('', 0.0)
    best_p, best_c = KEY_FREQS[0][0], 1e9
    for p, f in KEY_FREQS:
        c = 1200.0 * math.log2(freq / f) - offset_cents
        if abs(c) < abs(best_c):
            best_p, best_c = p, c
    return (best_p, best_c)


def refine_onsets(a: np.ndarray, onsets, rate: int,
                  search_s: float = 0.015) -> list[int]:
    """把每个起音位置精确到样本级。

    粗定位是「通量帧的中心」，误差能到半个窗（约 10ms）——
    弹得快的时候（十六分音符 90ms 一个）这 10ms 就够把前一个音的尾巴
    算进分析窗，音高直接测歪、甚至整个丢掉。
    在粗定位附近找**能量上升最陡**的那一点，把它修正过来。
    """
    n = len(a)
    span = int(search_s * rate)
    smooth = max(3, int(0.0015 * rate))       # 1.5ms 滑动平均，压毛刺
    k = np.ones(smooth) / smooth
    out: list[int] = []
    for pos in onsets:
        lo = max(0, pos - span)
        hi = min(n, pos + span)
        if hi - lo < smooth * 3:
            out.append(pos)
            continue
        env = np.convolve(np.abs(a[lo:hi]), k, mode='same')
        d = np.diff(env)
        if len(d) == 0:
            out.append(pos)
            continue
        out.append(lo + int(np.argmax(d)))
    return out


def estimate_f0_peak_ex(seg: np.ndarray, rate: int,
                        ratio: float = 0.15,
                        fmin: float = 105.0) -> tuple[float, float]:
    """返回 (基频, **谱峰突出度 dB**)。

    突出度 = 那根柱子比它左右各 25 个 bin 的平均高出多少 dB。
    真琴音是"一根尖柱"（基频、二次、三次谐波都是独立峰），
    背景音乐/人声是"一片糊"，这个值会低得多 —— 实测能差出 10 dB 以上。

    ★ fmin 为什么是 105 Hz ★
      琴的最低音 `1` 是 C3 = 130.8Hz。以前这里写 60Hz，是当初误以为
      最低音是 C4 时留下的 —— 结果低音区老是被 60~100Hz 的假峰带跑：
      起音包络（快起音 + 指数衰减）本身会在极低频堆出能量，
      实测把 `1` 测成了 67.8Hz（正好一半）。卡在 105 就干净了，
      同时给整体降调的录音留了 20% 余量。
    """
    x = np.asarray(seg, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x - x.mean()
    m = len(x)
    if m < 256 or not np.any(x) or rate <= 0:
        return (0.0, 0.0)
    spec = np.abs(np.fft.rfft(x * np.hanning(m)))
    if spec.max() <= 0:
        return (0.0, 0.0)
    freqs = np.fft.rfftfreq(m, 1.0 / rate)
    thr = float(spec.max()) * ratio
    for i in range(2, len(spec) - 2):
        if freqs[i] < fmin:
            continue
        if (spec[i] >= spec[i - 1] and spec[i] >= spec[i + 1]
                and spec[i] > thr):
            lo = max(0, i - 25)
            hi = min(len(spec), i + 26)
            around = float(spec[lo:hi].mean())
            db = 20.0 * math.log10((float(spec[i]) + 1e-12)
                                   / (around + 1e-12))
            f = float(freqs[i])
            # ★ 抛物线插值：弹得快时窗口只能取很短，bin 会粗到 10Hz 以上
            #   （130Hz 就是 ±60 音分），光靠 bin 中心根本对不准。
            #   拿峰和左右两个邻居拟合抛物线，能把峰位修正到 bin 之间。
            y0 = float(spec[i - 1])
            y1 = float(spec[i])
            y2 = float(spec[i + 1])
            den = y0 - 2.0 * y1 + y2
            if abs(den) > 1e-12:
                frac = 0.5 * (y0 - y2) / den
                if -0.5 <= frac <= 0.5:
                    f += frac * (float(freqs[1]) - float(freqs[0]))
            return (f, db)
    return (0.0, 0.0)


def estimate_f0_peak(seg: np.ndarray, rate: int,
                     ratio: float = 0.15,
                     fmin: float = 105.0) -> float:
    """估基频：幅度谱里**最低的那根够强的柱子**。

    ★ 为什么这件乐器用这个而不是 HPS ★
      这些采样里同时存在基频、半频和一堆相邻的峰（130 和 258/261 并存），
      HPS 那种"把整数倍频率的谱值连乘"的办法会被带跑 ——
      实测它把 `1`(130Hz) 认成 261Hz、把 `6`(219Hz) 认成 439Hz，正好差八度。
      而"最低的那根柱子就是基频"这条朴素规则，16 个采样全部命中。
    """
    return estimate_f0_peak_ex(seg, rate, ratio, fmin)[0]


def key_scores(seg: np.ndarray, rate: int, offset_cents: float = 0.0,
               harmonics: int = 3) -> list[tuple[str, float, float]]:
    """给琴上 16 个键各打一个「像不像」的分（对数域，越大越像）。

    ★ 为什么不用「先估基频再找最近的键」★
      那样一遇到谐波干扰就会估出个 220Hz 来（440 的一半，HPS 的老毛病），
      然后匹配到完全不相干的键上。

    ★ 为什么谐波只数前 3 个 ★（这个是实测踩出来的）
      数到 8 个的时候，高音区**全被认成低八度**：`1'` 的信号在 523/1046
      上有能量，而 `1` 的模板正好把 523/1046 当成自己的 2 次、4 次谐波，
      两边的分数咬得极近；再往后高音键的 5~8 次谐波早就超出有效带宽、
      只剩噪声，把它的平均分往下拖，于是低八度反超。
      砍到 3 个谐波，"基频在哪儿"就成了决定因素，八度问题当场消失。
    """
    x = np.asarray(seg, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x - x.mean()
    m = len(x)
    if m < 256 or not np.any(x) or rate <= 0:
        return []
    spec = np.abs(np.fft.rfft(x * np.hanning(m)))
    if spec.max() <= 0:
        return []
    n_bins = len(spec)
    scale = 2.0 ** (offset_cents / 1200.0)
    # 太弱的谐波别拿 1e-12 去罚它（那会把平均值拖到 -27），
    # 统一按「比峰值低 80 分贝」算就够了。
    floor = float(spec.max()) * 1e-4

    out: list[tuple[str, float, float]] = []
    for pitch, f0 in UNIQUE_KEYS:
        f = f0 * scale
        logsum = 0.0
        wsum = 0.0
        for h in range(1, harmonics + 1):
            k = int(round(f * h * m / float(rate)))
            if k <= 0 or k >= n_bins:
                break
            v = max(float(spec[max(0, k - 1):k + 2].max()), floor)
            # ★ 基频说话最算数，谐波越远越只是参考 ★
            #   因为八度关系的两个键（1 和 8、5 和 5'）谐波大面积重合，
            #   不偏袒基频的话两者分数会咬得死死的，来回乱跳。
            w = 1.0 / h
            logsum += w * math.log(v)
            wsum += w
        if wsum <= 0:
            continue
        out.append((pitch, logsum / wsum, f0))    # 加权平均
    out.sort(key=lambda t: -t[1])
    return out


def match_key(seg: np.ndarray, rate: int, offset_cents: float = 0.0,
              max_cents: float = 60.0) -> tuple[str, float, float]:
    """从琴上挑一个最像的键，返回 (音高, 理论频率, **置信度 dB**)。

    音高来自「最低的那根强谱峰 → 最近的键」；
    置信度就是那根柱子有多突出（见 `estimate_f0_peak_ex`）。

    ★ 别再用「谐波打分第一名 vs 第二名」当置信度 ★
      实测它对这件乐器几乎没区分度：八度关系的两个键（1 和 8）谐波大面积重合，
      真正的琴音照样能判出负分。现在改成看谱峰锐度，真琴音 ≳ 12 dB，
      背景音乐/人声通常 < 8 dB。
    """
    f0, prominence = estimate_f0_peak_ex(seg, rate)
    if f0 <= 0:
        return ('', 0.0, 0.0)
    pitch, cents = nearest_pitch(f0, offset_cents)
    if not pitch or abs(cents) > max_cents:
        return ('', 0.0, 0.0)
    return (pitch, pitch_freq(pitch), prominence)


def estimate_offset_cents(freqs) -> float:
    """估整体跑调多少音分 —— B 站视频转码 / 变速常常整体偏高偏低。

    做法：把每个频率换成 MIDI 音高，只看它的小数部分（也就是"离最近的
    半音差多少"），然后求**圆形平均**。为什么不用普通平均：
    -0.49 和 +0.49 明明差不多，算术平均却会得到 0。
    """
    vals = [f for f in freqs if f and f > 0]
    if len(vals) < 4:
        return 0.0
    fracs = []
    for f in vals:
        midi = 69.0 + 12.0 * math.log2(f / 440.0)
        fracs.append(midi - round(midi))
    two_pi = 2.0 * math.pi
    s = sum(math.sin(two_pi * x) for x in fracs)
    c = sum(math.cos(two_pi * x) for x in fracs)
    if abs(s) < 1e-12 and abs(c) < 1e-12:
        return 0.0
    return math.atan2(s, c) / two_pi * 100.0


def guess_bpm(audio: np.ndarray, rate: int,
              lo: int = 55, hi: int = 200) -> int:
    """从起音间隔粗猜曲速（拍/分）。

    只为了把间隔量化成好看的拍数 —— 猜得不准用户还能手改。
    原理：所有间隔都应该是「十六分音符」的整数倍，扫一遍 BPM 看哪个最贴合。
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    onsets = detect_onsets(np.asarray(audio, dtype=np.float64), rate)
    if len(onsets) < 6:
        return 0
    d = np.diff(np.asarray(onsets, dtype=np.float64)) / float(rate)
    d = d[(d > 0.05) & (d < 3.0)]
    if len(d) < 5:
        return 0

    best_bpm, best_score = 0, -1e18
    for bpm in range(lo, hi + 1):
        unit = 60.0 / bpm / 4.0                 # 十六分音符多长
        r = d / unit
        err = np.abs(r - np.round(r))
        score = float(np.sum(np.clip(1.0 - err / 0.15, 0.0, 1.0)))
        if np.sum(err < 0.15) < 3:              # 贴合的太少，不信
            continue
        score -= abs(bpm - 120) * 1e-3          # 同样贴合时挑靠近 120 的
        if score > best_score:
            best_score, best_bpm = score, bpm
    return best_bpm


# ---------------------------------------------------------------- 主流程

def transcribe(audio: np.ndarray, rate: int,
               bpm: int = 120,
               snap: float = 0.25,
               win_s: float = 0.20,
               max_cents: float = 60.0,
               min_margin: float = DEFAULT_MIN_MARGIN,
               calibrate: bool = True,
               onset_ratio: float = 0.45,
               onset_min_gap: float = 0.08,
               info: dict | None = None) -> tuple[list[str], list[NoteHit]]:
    """把音频转成 (token 列表, 识别详情)。

    参数
        bpm    用来把秒换算成拍
        snap   量化精度（拍），0.25 = 十六分音符
        win_s  每个音取多长来分析音高
        max_cents  偏差超过这么多音分就当作"不是琴声"丢掉
        min_margin 置信度（第一名和第二名的分差）低于这个就丢掉 ——
                   调高能滤掉背景音乐，调低能多捞回弱音
        calibrate  先估一次整体跑调量再匹配（B站视频转码/变速常整体偏）
        info   传个 dict 进来的话，会把诊断信息写进去
    """
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    a = np.asarray(audio, dtype=np.float64)
    if len(a) == 0 or rate <= 0:
        return ([], [])

    onsets = detect_onsets(a, rate, thresh_ratio=onset_ratio,
                           min_gap_s=onset_min_gap)
    if not onsets:
        return ([], [])
    # 把起点精确到样本级 —— 弹得快时差这 10ms 就全乱
    onsets = refine_onsets(a, onsets, rate)

    spb = 60.0 / max(1, bpm)
    win = max(256, int(win_s * rate))

    # ★ 先粗采一遍基频，看整体跑调多少 ★
    #   不先校正的话，整体偏 60 音分的录音会被硬塞到隔壁键上，整首全错。
    offset = 0.0
    if calibrate:
        step = max(1, len(onsets) // 60)
        rough = []
        for pos in onsets[::step]:
            f0 = f0_hps(a[pos:pos + win], rate)
            if f0 > 0:
                rough.append(f0)
        offset = estimate_offset_cents(rough)

    hits: list[NoteHit] = []
    dropped = 0
    hit_margins: list[float] = []
    drop_margins: list[float] = []
    # 窗口至少要有这么长，否则 FFT 分辨率太烂（45ms ≈ 130Hz 的 6 个周期）
    min_win = max(2048, int(0.045 * rate))
    for k, pos in enumerate(onsets):
        end = pos + win
        if k + 1 < len(onsets):
            # ★ 下一个音就在跟前的话，窗口只取到它之前一点 ★
            #   不然窗里混着两个音，「最低谱峰」测出来的是谁都不好说 ——
            #   弹快的时候这就是"漏音 + 错位"的主因。
            end = min(end, max(pos + min_win,
                               onsets[k + 1] - int(0.006 * rate)))
        seg = a[pos:end]
        if len(seg) < 256:
            continue
        # 注：试过给分析窗加「前重后轻」的衰减斜坡来压掉下一个音的干扰，
        #     结果反而更差（认出 125 个 vs 原本 160 个）——
        #     衰减等于缩短有效窗长、频率分辨率跟着降，「最低谱峰」更不准了。
        #     别再走这条弯路，改用上面那个「按下一个音的远近动态截断」。
        pitch, f_theory, margin = match_key(seg, rate, offset, max_cents)
        # 注意：置信度可能是负的（打分第一名跟"最低谱峰"给出的音高不一致）。
        # 只有真的设了门槛（> 0）才拿它过滤，不然 min_margin=0 会误杀一片。
        if not pitch or (min_margin > 0 and margin < min_margin):
            dropped += 1
            drop_margins.append(margin)
            continue
        # 偏差音分：拿「最低谱峰」跟理论值比（纯粹是显示用，不再拿来丢弃）
        f_meas = estimate_f0_peak(seg, rate) or f_theory
        cents = 1200.0 * math.log2(f_meas / f_theory)
        while cents > 600.0:
            cents -= 1200.0
        while cents < -600.0:
            cents += 1200.0
        hits.append(NoteHit(time=pos / rate, pitch=pitch,
                            freq=f_theory, cents=cents))
        hit_margins.append(margin)

    if info is not None:
        import statistics
        info['offset_cents'] = offset
        info['onsets'] = len(onsets)
        info['dropped'] = dropped
        info['seconds'] = len(a) / float(rate)
        if hit_margins:
            info['margin_hit'] = statistics.median(hit_margins)
        if drop_margins:
            info['margin_drop'] = statistics.median(drop_margins)
        if hit_margins and drop_margins:
            allm = sorted(hit_margins + drop_margins)
            info['margin_all'] = allm

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

    # ★ 音符本身最多占 MAX_NOTE_BEATS 拍，多出来的时间写成休止符 ★
    #   不这么干的话，"弹一下 → 停 10 拍"会被写成一个 10 拍的超长音
    #   （`3----------`），读起来莫名其妙，时间轴上也是一根巨型色块。
    tokens: list[str] = []
    cursor = 0.0
    for h in hits:
        b = round(h.beat / snap) * snap
        pause = b - cursor
        if pause > 1e-6:
            tokens.extend(encode.split_gap(pause))
        note_beats = max(snap, min(h.beats, MAX_NOTE_BEATS))
        tokens.append(encode.token_for(note_beats, h.pitch))
        cursor = b + note_beats

    return (tokens, hits)


def to_sheet_text(audio: np.ndarray, rate: int, bpm: int = 120,
                  snap: float = 0.25, title: str = '听音记谱',
                  min_margin: float = DEFAULT_MIN_MARGIN,
                  onset_ratio: float = 0.45,
                  onset_min_gap: float = 0.08,
                  info: dict | None = None) -> tuple[str, list[NoteHit]]:
    """直接生成可以贴进编辑器的谱面文本。"""
    tokens, hits = transcribe(audio, rate, bpm=bpm, snap=snap,
                              min_margin=min_margin,
                              onset_ratio=onset_ratio,
                              onset_min_gap=onset_min_gap, info=info)
    if not tokens:
        return ('', [])
    body = ' '.join(tokens)
    lines = [body[i:i + 200] for i in range(0, len(body), 200)]
    text = '%s\n#%d#\n%s' % (title, bpm, '\n'.join(lines))
    return (text, hits)
