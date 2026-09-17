# -*- coding: utf-8 -*-
"""实时跟弹 —— 边听边认，认出就报。

跟离线版 `transcribe` 用的是同一套算法（**谱通量找起点 + 最低谱峰定音高**），
区别是全部在一个滚动窗口里做：喂进来一块音频，就吐出来这块里认到的音。

延迟大约 0.25 秒 —— 因为音高分析要等起点之后的一小段样本攒够。
对"看着浮窗跟弹"来说完全够用。

★ 为什么必须留一道置信度门槛（min_margin，单位 dB）★
    loopback 录的是**整个输出设备**上的声音 —— 你放的背景音乐、网页视频、
    语音通话，全都在这儿。而"最低谱峰 → 最近的琴键"这套办法对**任何**
    有音高的声音都能凑出一个答案（音乐的音高是连续的，总有个键在 60 音分内）。
    所以不过滤的话，放首歌都能给你"弹"出一整首谱子来。
    置信度取的是「基频谱峰比周围平均高出多少 dB」：
    干净的游戏采样 22~26 dB，噪声/伴奏做出来的假峰低得多，门槛卡 8 正好。

两种用法
--------
    跟弹监视：只听不录，游戏里弹一下浮窗就亮一下（找手感 / 校准键位）
    听音记谱：录音的同时实时显示，弹完再统一转成谱子
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .transcribe import estimate_f0_peak, match_key


class LiveDetector:
    """流式音符检测器。

    push(音频块) -> [(绝对秒数, 音高, 实测频率), ...]
    """

    def __init__(self, rate: int = 48000, hop: int = 256, win: int = 1024,
                 thresh_ratio: float = 0.45, min_gap_s: float = 0.07,
                 local_win_s: float = 0.35, f0_win_s: float = 0.20,
                 max_cents: float = 60.0, gate_ratio: float = 0.04,
                 dedup_s: float = 0.12, min_margin: float = 8.0):
        self.rate = int(rate)
        self.hop = int(hop)
        self.win = int(win)
        self.thresh_ratio = float(thresh_ratio)
        self.min_gap_s = float(min_gap_s)
        self.max_cents = float(max_cents)
        self.gate_ratio = float(gate_ratio)
        self.dedup_s = float(dedup_s)
        self.min_margin = float(min_margin)
        self.f0_len = max(256, int(f0_win_s * self.rate))

        local_frames = max(3, int(local_win_s * self.rate / self.hop))
        self._frames: deque[tuple[int, float]] = deque(maxlen=local_frames + 2)

        self._w = np.hanning(self.win)
        self._tail = np.zeros(0, dtype=np.float64)
        self._raw = np.zeros(0, dtype=np.float64)
        self._keep = int((local_win_s + f0_win_s + 0.30) * self.rate)
        self._abs_pos = 0          # _raw[0] 对应的总样本号
        self._total = 0            # 已经喂进来的总样本数
        self._frame_pos = 0        # 下一个要算的帧的起点
        self._prev_spec = None
        self._cands: list[tuple[float, int, float]] = []   # 待定案的候选
        self._waiting: list[int] = []    # 等 f0 窗口攒够的起点（绝对样本号）
        self._last_settled_t = -9.0      # 上次定案的时间（做不应期）
        self._last_pitch_t: dict[str, float] = {}
        self._rms_peak = 1e-6
        # 内部流水（排查漏音用）：(时间, 通量, 是否被当成局部峰)
        self.log: deque[tuple[float, float, bool]] = deque(maxlen=8000)
        # 被丢掉的 onset 及原因
        self.rejects: deque[tuple[float, str]] = deque(maxlen=500)

    # ---------------- 主入口 ----------------

    def push(self, block) -> list[tuple[float, str, float]]:
        x = np.asarray(block, dtype=np.float64)
        if x.ndim > 1:
            x = x.mean(axis=1)
        x = np.ascontiguousarray(x).ravel()
        if len(x) == 0:
            return []

        self._total += len(x)
        rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        self._rms_peak = max(self._rms_peak * 0.9995, rms)

        # 原始样本滚动缓冲（音高分析要从这儿取）
        self._raw = np.concatenate([self._raw, x])
        if len(self._raw) > self._keep:
            cut = len(self._raw) - self._keep
            self._raw = self._raw[cut:]
            self._abs_pos += cut

        # 逐 hop 算谱通量
        self._tail = np.concatenate([self._tail, x])
        while len(self._tail) >= self.win:
            frame = self._tail[:self.win] * self._w
            self._tail = self._tail[self.hop:]
            pos = self._frame_pos
            self._frame_pos += self.hop
            spec = np.abs(np.fft.rfft(frame))
            flux = 0.0
            if self._prev_spec is not None:
                flux = float(np.sum(np.maximum(spec - self._prev_spec, 0.0)))
            self._prev_spec = spec
            self._frames.append((pos, flux))
            if len(self._frames) >= 3:
                self._check_onset()

        return self._collect()

    # ---------------- 内部 ----------------

    def _check_onset(self):
        """看「上一帧」是不是一个起音（需要它前后各一帧才能判定）。

        ★ 为什么不当场就报出来 ★
          一记音头在通量上其实是**一小簇**局部峰（音色上升期抖几下），
          当场报就会一个音报成两三个。离线版的做法是"先全收着，
          再按间距挑最强的" —— 这里照搬，只是换成滑动窗口：
          候选先攒着，过了 min_gap 秒还没有更强的，才让最强的那个定案。
        """
        _p_prev, f_prev = self._frames[-3]
        pos_mid, f_mid = self._frames[-2]
        _p_next, f_next = self._frames[-1]
        is_peak = (f_mid >= f_prev and f_mid >= f_next)
        self.log.append(((pos_mid + self.win // 2) / float(self.rate),
                         f_mid, is_peak))
        if not is_peak:
            return                                   # 不是局部峰
        local = max(f for _p, f in self._frames)
        if local <= 0 or f_mid / local < self.thresh_ratio:
            return
        t = (pos_mid + self.win // 2) / float(self.rate)

        # 不应期：刚定案过，这一段里的候选一律不接（不然同一记音头
        # 拖出来的抖动尾巴会另起一簇，变成"一个音报两次"）
        if t < self._last_settled_t + self.min_gap_s:
            return

        self._cands.append((f_mid, pos_mid + self.win // 2, t))

        # 最早的候选后面 min_gap 秒内不可能再冒新的了（现在都过了这么久）
        # -> 这一簇可以定案：挑最强的，整簇一起吃掉
        if t - self._cands[0][2] < self.min_gap_s:
            return
        best = max(self._cands, key=lambda c: c[0])
        self._waiting.append(best[1])
        self._last_settled_t = best[2]
        self._cands = [c for c in self._cands
                       if c[2] >= best[2] + self.min_gap_s]

    def _flush_cands(self):
        """把还挂着的候选里最强的一个定案（收尾时用）。"""
        if self._cands:
            best = max(self._cands, key=lambda c: c[0])
            self._waiting.append(best[1])
            self._last_settled_t = best[2]
            self._cands = [c for c in self._cands if c[2] > best[2] + 1e-9]

    def flush(self) -> list[tuple[float, str, float]]:
        """收尾：把还挂着的候选定案（停止监听前调一次）。"""
        self._flush_cands()
        return self._collect()

    def _collect(self) -> list[tuple[float, str, float]]:
        # 攒够音高窗口的挑出来处理 —— 不能因为队首没攒够就卡住后面的
        ready = [at for at in self._waiting
                 if at + self.f0_len <= self._total]
        if ready:
            self._waiting = [at for at in self._waiting
                             if at + self.f0_len > self._total]

        out: list[tuple[float, str, float]] = []
        for at in sorted(ready):
            tt = at / float(self.rate)
            if at < self._abs_pos:
                self.rejects.append((tt, '太老（缓冲已经滑走）'))
                continue                             # 太老了，丢了
            i = at - self._abs_pos
            seg = self._raw[i:i + self.f0_len]
            if len(seg) < 128:
                self.rejects.append((tt, '片段太短'))
                continue
            if len(seg) < self.f0_len:
                seg = np.pad(seg, (0, self.f0_len - len(seg)))
            # 绝对静音门限：别把底噪当音符报出来
            rms = float(np.sqrt(np.mean(seg * seg)))
            if rms < self._rms_peak * self.gate_ratio:
                self.rejects.append(
                    (tt, '太轻 rms=%.5f < %.5f' % (rms, self._rms_peak
                                                   * self.gate_ratio)))
                continue
            pitch, f_theory, margin = match_key(seg, self.rate)
            # 置信度可能是负的，只有设了门槛才拿它过滤
            if not pitch or (self.min_margin > 0
                             and margin < self.min_margin):
                self.rejects.append(
                    (tt, '置信度不够 %.3f' % margin))
                continue
            t = tt
            # 同一个音高在 dedup 秒内不重复报（抖动的第二簇会撞在这儿）
            if t - self._last_pitch_t.get(pitch, -9.0) < self.dedup_s:
                self.rejects.append((tt, '同音高去重 %s' % pitch))
                continue
            self._last_pitch_t[pitch] = t
            out.append((t, pitch, f_theory))
        return out

    def reset(self):
        self._tail = np.zeros(0, dtype=np.float64)
        self._raw = np.zeros(0, dtype=np.float64)
        self._abs_pos = 0
        self._total = 0
        self._frame_pos = 0
        self._prev_spec = None
        self._frames.clear()
        self._cands.clear()
        self._waiting.clear()
        self._last_settled_t = -9.0
        self._last_pitch_t.clear()
        self._rms_peak = 1e-6
