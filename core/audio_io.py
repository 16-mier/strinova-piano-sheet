# -*- coding: utf-8 -*-
"""音频 / 视频 → 单声道波形数组。

什么格式都吃：
    1. soundfile（装了就用，wav / flac / ogg 走这条，最快）
    2. 标准库 wave（纯 wav 的兜底）
    3. **ffmpeg**（mp3 / m4a / aac / 甚至整个 mp4 视频直接抽音轨）

所以从 B 站下下来的视频不用先转音频，直接丢进来就行。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import wave

import numpy as np

DEFAULT_SR = 48000

# Windows 下别让 ffmpeg 弹黑窗口
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

_FFMPEG_HINTS = [
    r'C:\ffmpeg\bin\ffmpeg.exe',
    r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
]


def ffmpeg_exe() -> str:
    """找 ffmpeg 可执行文件；找不到返回 ''。"""
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    for p in _FFMPEG_HINTS:
        if os.path.isfile(p):
            return p
    # WinGet 装的，路径里带版本号，得扫一遍
    base = os.path.join(os.environ.get('LOCALAPPDATA', ''),
                        'Microsoft', 'WinGet', 'Packages')
    if os.path.isdir(base):
        try:
            for name in os.listdir(base):
                if 'ffmpeg' in name.lower():
                    for root, _dirs, files in os.walk(os.path.join(base, name)):
                        if 'ffmpeg.exe' in files:
                            return os.path.join(root, 'ffmpeg.exe')
        except OSError:
            pass
    return ''


def resample(a: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """线性重采样 —— 我们只看音高，不需要发烧级音质。"""
    if sr_in == sr_out or len(a) == 0:
        return a
    n_out = int(round(len(a) * sr_out / float(sr_in)))
    if n_out <= 0:
        return a
    x_old = np.linspace(0.0, 1.0, len(a), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, a)


def _load_wav(path: str):
    with wave.open(path, 'rb') as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype='<i4').astype(np.float64) / 2147483648.0
    else:
        return None
    if ch > 1:
        a = a[:len(a) // ch * ch].reshape(-1, ch).mean(axis=1)
    return a, sr


def _load_ffmpeg(path: str, target_sr: int):
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError(
            '系统里找不到 ffmpeg，解码不了这个格式。\n'
            '装一个（winget install Gyan.FFmpeg）或者换成 wav 文件。')
    cmd = [exe, '-v', 'error', '-nostdin', '-i', path,
           '-vn', '-ac', '1', '-ar', str(target_sr), '-f', 'f32le', '-']
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        msg = proc.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError('ffmpeg 解码失败：%s' % (msg[:400] or '未知原因'))
    a = np.frombuffer(proc.stdout, dtype='<f4').astype(np.float64)
    if len(a) == 0:
        raise RuntimeError('这个文件里没有音轨（或者音轨是空的）')
    return a, target_sr


def load_audio(path: str, target_sr: int | None = DEFAULT_SR):
    """读音频 / 视频，返回 (单声道 float64 波形, 采样率)。

    target_sr 传 None = 保持文件原本的采样率（播音时要原样送出去）。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError('找不到文件：%s' % path)

    # 1) soundfile（快，格式支持也够广）
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype='float64', always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        sr = int(sr)
        if target_sr and sr != target_sr:
            data = resample(data, sr, target_sr)
            sr = target_sr
        return np.ascontiguousarray(data, dtype=np.float64), sr
    except Exception:
        pass

    # 2) 纯 wav 用标准库就够
    if path.lower().endswith('.wav'):
        try:
            got = _load_wav(path)
            if got:
                a, sr = got
                if target_sr and sr != target_sr:
                    a = resample(a, sr, target_sr)
                    sr = target_sr
                return np.ascontiguousarray(a, dtype=np.float64), int(sr)
        except Exception:
            pass

    # 3) 交给 ffmpeg 收拾
    return _load_ffmpeg(path, int(target_sr or DEFAULT_SR))


def describe(path: str) -> str:
    """一句话说明能读到什么（诊断用）。"""
    try:
        a, sr = load_audio(path)
        return ('%s｜%.1f 秒｜%d Hz｜峰值 %.3f'
                % (os.path.basename(path), len(a) / sr, sr,
                   float(np.abs(a).max()) if len(a) else 0.0))
    except Exception as e:
        return '%s｜读取失败：%s' % (os.path.basename(path), e)
