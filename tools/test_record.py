# -*- coding: utf-8 -*-
"""自检：loopback 录音能不能用、哪个设备录得到声音。

用法：python tools/test_record.py [每个设备录几秒]
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from core import recorder                 # noqa: E402

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0


def main() -> int:
    if not recorder.available():
        print('soundcard 库没装：pip install soundcard')
        return 1

    devs = recorder.list_output_devices()
    print('可做 loopback 的输出设备 %d 个\n' % len(devs))
    print('开始逐个试录 %.1f 秒…\n' % SECS)

    ok_list = []
    for name, dev_id in devs:
        rec = recorder.LoopbackRecorder()
        started = rec.start(dev_id, channels=2)
        if not started:
            rec.abort()
            started = rec.start(dev_id, channels=1)
        if not started:
            print('  [--] %-42s 开不了: %s'
                  % (name[:42], (rec.last_error or '')[:60]))
            continue
        time.sleep(SECS)
        data = rec.stop()
        rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
        peak = float(np.abs(data).max()) if len(data) else 0.0
        flag = '   <- 现在有声音' if peak > 0.01 else ''
        print('  [OK] %-42s %6d 样本  RMS=%.5f  峰值=%.4f%s'
              % (name[:42], len(data), rms, peak, flag))
        ok_list.append(name)

    print('\n能录的设备：%d / %d' % (len(ok_list), len(devs)))
    print('\n提示：要抓游戏声音，选游戏音频输出的那个设备。'
          '如果哪个都没声音，就在试的时候放点音乐确认一下链路。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
