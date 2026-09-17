# -*- coding: utf-8 -*-
"""端到端自测：往某个输出设备播一段音符，同时 loopback 监听它，
看「实时跟弹」这条链路能不能认出来。

这比喂合成数组真实得多 —— 走的是和游戏里一模一样的路子
（设备 → WASAPI loopback → LiveDetector）。

用法：
    python tools/test_live_loopback.py              # 自动挑设备
    python tools/test_live_loopback.py 耳机          # 按名字关键字挑
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import numpy as np                                   # noqa: E402

from core import audio_io, broadcast, live, recorder, synth  # noqa: E402

RATE = 48000
SEQ = [('1', 0.6), ('3', 0.6), ('5', 0.6), ('6', 0.6),
       ('1', 0.35), ('1', 0.35), ('5', 0.8)]


def render() -> np.ndarray:
    """用游戏原始采样拼出测试旋律。"""
    notes = synth.ensure_notes(os.path.join(ROOT, 'assets', 'notes'))
    total = int(sum(b for _p, b in SEQ) * RATE) + RATE // 4
    out = np.zeros(total, dtype=np.float64)
    cur = 0
    for pitch, beats in SEQ:
        path = notes.get(pitch)
        if not path:
            cur += int(beats * RATE)
            continue
        one, _sr = audio_io.load_audio(path, target_sr=RATE)
        n = min(int(beats * RATE * 0.85), len(one))
        out[cur:cur + n] += one[:n] * 0.5
        cur += int(beats * RATE)
    return out


def pick_device(kw: str):
    devs = recorder.list_output_devices()
    if kw:
        for name, dev in devs:
            if kw.lower() in name.lower():
                return name, dev
    for pref in ('耳机', 'headphone', 'shure', '扬声器', 'speaker'):
        for name, dev in devs:
            if pref in name.lower() and not broadcast.is_virtual(name):
                return name, dev
    for name, dev in devs:
        if not broadcast.is_virtual(name):
            return name, dev
    return (devs[0] if devs else (None, None))


def main() -> int:
    kw = sys.argv[1] if len(sys.argv) > 1 else ''
    name, dev = pick_device(kw)
    if not dev:
        print('找不到可用的输出设备')
        return 1
    print('目标设备：%s' % name)
    print('测试旋律：%s' % ' '.join(p for p, _b in SEQ))

    got: list[tuple[float, str, float]] = []
    det = live.LiveDetector(rate=RATE)
    blocks = {'n': 0}

    def on_block(block):
        blocks['n'] += 1
        for t, pitch, freq in det.push(block):
            got.append((t, pitch, freq))

    stream = recorder.LoopbackStream(on_block, blocksize=1024,
                                     samplerate=RATE)
    if not stream.start(str(dev), channels=2):
        stream.stop()
        if not stream.start(str(dev), channels=1):
            print('开不了 loopback：%s' % (stream.last_error or '未知'))
            return 1
    print('loopback 已开，2 秒后开始播…')
    time.sleep(2.0)

    wave = render()
    tmp = os.path.join(ROOT, '_live_test.wav')
    import wave as _w
    with _w.open(tmp, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((np.clip(wave, -1, 1) * 32767).astype('<i2').tobytes())

    caster = broadcast.Broadcaster()
    if not caster.start(tmp, [str(dev)], volume=0.55):
        print('播放失败：%s' % caster.errors)
        stream.stop()
        return 1

    t_end = time.time() + len(wave) / RATE + 1.5
    while time.time() < t_end:
        time.sleep(0.1)
    caster.stop()
    time.sleep(0.4)
    got.extend(det.flush())
    stream.stop()

    print('-' * 60)
    print('收到音频块 %d 个' % blocks['n'])
    for t, pitch, freq in got:
        print('  +%.3fs  %-4s  %6.1f Hz' % (t, pitch, freq))
    want = [p for p, _b in SEQ]
    have = [p for _t, p, _f in got]
    print('-' * 60)
    print('期望 %d 个：%s' % (len(want), ' '.join(want)))
    print('认出 %d 个：%s' % (len(have), ' '.join(have)))
    if not have:
        print('[FAIL] 一个都没认出来 —— 检查设备选对没、音量够不够')
        return 1
    j = 0
    ok = True
    for p in have:
        while j < len(want) and want[j] != p:
            j += 1
        if j >= len(want):
            ok = False
            break
        j += 1
    print('[%s] 顺序%s' % ('PASS' if ok else 'FAIL',
                           '正确（是期望序列的子序列）' if ok else '乱了'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
