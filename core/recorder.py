# -*- coding: utf-8 -*-
"""录音 —— 抓「游戏正在播的声音」。

为什么不用 sounddevice
---------------------
sounddevice（PortAudio）在 0.5.6 的 `WasapiSettings` **没有 loopback 选项**，
而 WASAPI 下每个输出设备的输入通道数是 0，直接开 InputStream 会报
`Invalid number of channels`。实测确认过，所以改用 **soundcard** 库，
它对 WASAPI loopback 是一等支持（`include_loopback=True`）。

★ 录的是什么 ★
  某个**输出设备**上正在播的一切。所以别的程序如果也在往同一个设备出声
  （音乐播放器、语音……），会一并录进来，识别时就可能多出音符。
  最干净的做法是让游戏单独走一个输出设备。
"""

from __future__ import annotations

import threading

import numpy as np

# ★ soundcard 必须延迟导入 ★
#   它在导入时就会把线程的 COM 初始化成 MTA，而 Qt 需要 STA ——
#   一旦在 QApplication 之前导入，Qt 启动会直接失败：
#       QWindowsContext: OleInitialize() failed: COM error 0x80010106
#   所以改成"真正要录音时才加载"。
_sc = None
_sc_tried = False


def _soundcard():
    global _sc, _sc_tried
    if not _sc_tried:
        _sc_tried = True
        try:
            import soundcard as sc
            _sc = sc
        except Exception:
            _sc = None
    return _sc


def available() -> bool:
    return _soundcard() is not None


def list_output_devices() -> list[tuple[str, str]]:
    """能用来做 loopback 的输出设备：[（显示名, 设备id）]。"""
    sc = _soundcard()
    if sc is None:
        return []
    out: list[tuple[str, str]] = []
    try:
        for spk in sc.all_speakers():
            out.append((spk.name, str(spk.id)))
    except Exception:
        pass
    return out


class LoopbackRecorder:
    """把某个输出设备正在播的音频录下来。"""

    def __init__(self, samplerate: int = 48000):
        self.samplerate = int(samplerate)
        self.recording = False
        self.last_error = ''
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # ---------------- 控制 ----------------

    def start(self, device_id: str, channels: int = 2) -> bool:
        sc = _soundcard()
        if sc is None:
            self.last_error = 'soundcard 库没装（pip install soundcard）'
            return False
        if self.recording:
            return True
        try:
            mic = sc.get_microphone(id=device_id, include_loopback=True)
        except Exception as e:
            self.last_error = '找不到这个设备：%r' % e
            return False
        if mic is None:
            self.last_error = '找不到这个设备'
            return False

        with self._lock:
            self._chunks = []
        self.last_error = ''
        self.recording = True
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(mic, channels), daemon=True)
        self._thread.start()
        # 等后台线程确认「录音流真的开起来了」再返回，
        # 否则调用方拿到 True 但实际什么都没录到，很难排查。
        self._ready.wait(timeout=2.0)
        if not self.recording:
            return False
        return True

    def _run(self, mic, channels: int):
        try:
            with mic.recorder(samplerate=self.samplerate,
                              channels=channels,
                              blocksize=4096) as rec:
                self._ready.set()          # 开成功了
                while self.recording:
                    data = rec.record(numframes=4096)
                    if data is None or not len(data):
                        continue
                    with self._lock:
                        self._chunks.append(np.array(data, copy=True))
        except Exception as e:
            self.last_error = str(e)
        finally:
            self.recording = False
            try:
                self._ready.set()
            except Exception:
                pass

    def stop(self) -> np.ndarray:
        """停止并返回单声道音频（float32）。"""
        self.recording = False
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
            self._thread = None
        with self._lock:
            chunks = list(self._chunks)
            self._chunks = []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        data = np.concatenate(chunks, axis=0)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data.astype(np.float32)

    def abort(self):
        self.recording = False
        t = self._thread
        if t is not None:
            t.join(timeout=1.0)
            self._thread = None
        with self._lock:
            self._chunks = []

    # ---------------- 状态 ----------------

    @property
    def seconds(self) -> float:
        with self._lock:
            n = sum(len(c) for c in self._chunks)
        return n / max(1, self.samplerate)


class LoopbackStream:
    """一直录，把音频块交给回调 —— 实时跟弹用。

    和 `LoopbackRecorder` 的区别：那个是"攒完这一整段再给你"，
    这个是"每 20 毫秒给你一小块"，适合边听边认。
    """

    def __init__(self, on_block, blocksize: int = 1024,
                 samplerate: int = 48000):
        self.on_block = on_block
        self.blocksize = int(blocksize)
        self.samplerate = int(samplerate)
        self.running = False
        self.last_error = ''
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self, device_id: str, channels: int = 2) -> bool:
        sc = _soundcard()
        if sc is None:
            self.last_error = 'soundcard 库没装（pip install soundcard）'
            return False
        if self.running:
            return True
        try:
            mic = sc.get_microphone(id=device_id, include_loopback=True)
        except Exception as e:
            self.last_error = '找不到这个设备：%r' % e
            return False
        if mic is None:
            self.last_error = '找不到这个设备'
            return False
        self.last_error = ''
        self.running = True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        args=(mic, channels), daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self.running

    def _run(self, mic, channels: int):
        try:
            with mic.recorder(samplerate=self.samplerate, channels=channels,
                              blocksize=self.blocksize) as rec:
                self._ready.set()
                while self.running:
                    data = rec.record(numframes=self.blocksize)
                    if data is None or not len(data):
                        continue
                    try:
                        self.on_block(np.asarray(data, dtype=np.float64))
                    except Exception:
                        pass          # 回调里出错不能把录音线程弄死
        except Exception as e:
            self.last_error = str(e)
        finally:
            self.running = False
            try:
                self._ready.set()
            except Exception:
                pass

    def stop(self):
        self.running = False
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
            self._thread = None


def find_device_id(keyword: str) -> str | None:
    """按名字片段找输出设备 id。"""
    kw = (keyword or '').lower()
    if not kw:
        return None
    for name, dev_id in list_output_devices():
        if kw in name.lower():
            return dev_id
    return None


def guess_game_device() -> str | None:
    """猜一个最可能是「游戏声音输出」的设备。"""
    devs = list_output_devices()
    # 优先挑具体的物理设备，而不是各种虚拟声卡
    for kw in ('shure', 'headphone', '耳机', 'usb audio', 'speaker', '扬声器'):
        for name, dev_id in devs:
            if kw in name.lower():
                return dev_id
    return devs[0][1] if devs else None
