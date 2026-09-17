# -*- coding: utf-8 -*-
"""播音 —— 把一段音频送到**指定的输出设备**（比如虚拟声卡 → 游戏麦克风）。

用途
----
    想让游戏里的队友听到你放的东西，就得把声音"喂"进麦克风。
    做法是：
        1. 装个虚拟声卡（Voicemeeter / VB-CABLE 都行）
        2. 这里选中那个虚拟输入设备（名字带 Voicemeeter / CABLE 的那个）
        3. 游戏里的麦克风选成对应的虚拟输出
    顺便还能勾一路"监听"，自己用耳机同步听到。

为什么用 soundcard 而不是 QMediaPlayer
--------------------------------------
    QMediaPlayer 只能往**系统默认设备**送，选不了设备。
    soundcard 能点名送到任意一个输出端点，一个文件还能同时送好几路。
    ⚠ soundcard 在 import 时会把 COM 设成 MTA，跟 Qt 的 STA 打架 ——
      所以这里**延迟导入**（就是下面那个 _soundcard()）。
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np

from . import audio_io

_sc = None

# 虚拟声卡的常见关键字 —— 界面上会把这些排在前面并标出来
VIRTUAL_HINTS = ('voicemeeter', 'cable', 'vb-audio', 'virtual', 'vac',
                 'loopback', 'stereo mix')


def _soundcard():
    """延迟导入 soundcard（见模块 docstring 里说的 COM 原因）。"""
    global _sc
    if _sc is None:
        import soundcard as sc
        _sc = sc
    return _sc


def is_virtual(name: str) -> bool:
    n = (name or '').lower()
    return any(h in n for h in VIRTUAL_HINTS)


def list_speakers() -> list[tuple[str, str]]:
    """所有输出设备 [(名字, id)]，虚拟声卡排在最前面。"""
    try:
        sc = _soundcard()
        got = [(str(sp.name), str(sp.id)) for sp in sc.all_speakers()]
    except Exception:
        return []
    virt = [x for x in got if is_virtual(x[0])]
    rest = sorted((x for x in got if not is_virtual(x[0])),
                  key=lambda x: x[0].lower())
    return virt + rest


def list_mics() -> list[tuple[str, str]]:
    """所有录音设备 [(名字, id)]（只是列出来给个参考）。"""
    try:
        sc = _soundcard()
        return [(str(m.name), str(m.id)) for m in sc.all_microphones()]
    except Exception:
        return []


class Broadcaster:
    """把音频文件播到一到多路输出设备上。

    典型用法（播音给游戏麦克风 + 自己耳机监听）：
        b = Broadcaster()
        b.start('曲子.wav', [虚拟设备的 id, 耳机的 id], volume=0.9)
        ...
        b.stop()
    """

    def __init__(self):
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._errors: list[str] = []
        self._started = 0.0
        self._duration = 0.0
        self._rate = 48000
        self.path = ''

    # ---- 状态 ----

    @property
    def playing(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def position(self) -> float:
        if not self._started:
            return 0.0
        if not self.playing:
            return self._duration
        return min(self._duration, time.time() - self._started)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    # ---- 播放 ----

    def start(self, path: str, device_ids, volume: float = 1.0,
              loop: bool = False) -> bool:
        """开始播。device_ids 可以是一个字符串或一串字符串（多路同播）。"""
        self.stop()
        if isinstance(device_ids, str):
            device_ids = [device_ids]
        device_ids = [d for d in device_ids if d]
        if not device_ids:
            self._errors = ['没有选择输出设备']
            return False

        try:
            data, sr = audio_io.load_audio(path, target_sr=None)
        except Exception as e:
            self._errors = ['读取音频失败：%s' % e]
            return False
        if len(data) == 0:
            self._errors = ['这个文件的音轨是空的']
            return False

        vol = max(0.0, min(2.0, float(volume)))
        if abs(vol - 1.0) > 1e-6:
            data = data * vol
        data = np.clip(data, -1.0, 1.0)

        self.path = path
        self._rate = int(sr)
        self._duration = len(data) / float(sr)
        self._errors = []
        self._stop.clear()
        self._started = time.time()
        self._threads = []
        for dev in device_ids:
            t = threading.Thread(target=self._play_one,
                                 args=(data, sr, str(dev), bool(loop)),
                                 daemon=True)
            t.start()
            self._threads.append(t)
        return True

    def _play_one(self, data: np.ndarray, sr: int, device_id: str,
                  loop: bool):
        sc = _soundcard()
        try:
            spk = sc.get_speaker(id=device_id)
        except Exception as e:
            self._errors.append('找不到设备 %s：%r' % (device_id, e))
            return
        block = 4096
        try:
            while True:
                with spk.player(samplerate=int(sr), channels=1) as p:
                    for i in range(0, len(data), block):
                        if self._stop.is_set():
                            return
                        p.play(np.ascontiguousarray(data[i:i + block]))
                if not loop or self._stop.is_set():
                    return
        except Exception as e:
            self._errors.append('%s：%r' % (device_id, e))

    def stop(self):
        self._stop.set()
        for t in self._threads:
            try:
                t.join(timeout=0.8)
            except Exception:
                pass
        self._threads = []
        self._started = 0.0

    # ---- 试听一小段（选设备时用，听一下对不对） ----

    def preview(self, device_id: str, seconds: float = 1.2) -> bool:
        """朝某个设备放一声短音，确认它通不通。"""
        try:
            sc = _soundcard()
            sr = 48000
            n = int(sr * seconds)
            t = np.arange(n) / sr
            tone = (0.25 * np.sin(2 * np.pi * 523.25 * t)
                    * np.exp(-3.0 * t)).astype(np.float64)
            spk = sc.get_speaker(id=str(device_id))
            threading.Thread(
                target=lambda: spk.play(tone, samplerate=sr, channels=1),
                daemon=True).start()
            return True
        except Exception as e:
            self._errors.append('%r' % e)
            return False


def guess_voice_output() -> str:
    """猜哪个设备是「喂给游戏麦克风」的那个（优先 Voicemeeter Input）。"""
    for name, dev_id in list_speakers():
        n = name.lower()
        if 'voicemeeter input' in n or 'vaio' in n:
            return dev_id
    for name, dev_id in list_speakers():
        if is_virtual(name):
            return dev_id
    return ''


def guess_headphone() -> str:
    """猜哪个是耳机（排除虚拟声卡）。"""
    got = [(n, i) for n, i in list_speakers() if not is_virtual(n)]
    if not got:
        return ''
    for kw in ('耳机', 'headphone', 'earphone', 'headset'):
        for n, i in got:
            if kw in n.lower():
                return i
    for kw in ('扬声器', 'speaker'):
        for n, i in got:
            if kw in n.lower():
                return i
    return got[0][1]
