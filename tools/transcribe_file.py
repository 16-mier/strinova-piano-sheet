# -*- coding: utf-8 -*-
"""命令行：音频 / 视频 -> 谱面文本（不开界面，方便调参和验证）。

用法：
    python tools/transcribe_file.py "某首歌.mp4"
    python tools/transcribe_file.py "x.wav" --bpm 140 --out 结果.txt
    python tools/transcribe_file.py "x.mp4" --debug      # 打出识别详情
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import numpy as np                                   # noqa: E402

from core import audio_io, transcribe                # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('path', help='音频或视频文件')
    ap.add_argument('--bpm', type=int, default=0, help='0 = 自动猜')
    ap.add_argument('--snap', type=float, default=0.25)
    ap.add_argument('--max-cents', type=float, default=60.0)
    ap.add_argument('--min-margin', type=float, default=0.0,
                    help='置信度门槛，调高更干净、调低多捞音')
    ap.add_argument('--out', default='')
    ap.add_argument('--no-calibrate', action='store_true')
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        print('找不到文件：%s' % args.path)
        return 2

    t0 = time.time()
    audio, rate = audio_io.load_audio(args.path)
    print('读入 %s' % audio_io.describe(args.path))
    print('  解码耗时 %.2f 秒' % (time.time() - t0))

    t1 = time.time()
    bpm = args.bpm or transcribe.guess_bpm(audio, rate) or 120
    print('曲速：%d BPM%s' % (bpm, '' if args.bpm else '（自动猜）'))

    info: dict = {}
    tokens, hits = transcribe.transcribe(
        audio, rate, bpm=bpm, snap=args.snap,
        max_cents=args.max_cents, min_margin=args.min_margin,
        calibrate=not args.no_calibrate, info=info)
    print('  分析耗时 %.2f 秒' % (time.time() - t1))

    print('-' * 62)
    print('时长        %.1f 秒' % info.get('seconds', 0.0))
    print('起音候选    %d 个' % info.get('onsets', 0))
    print('认出音符    %d 个' % len(hits))
    print('丢掉        %d 个（音高对不上琴键）' % info.get('dropped', 0))
    print('整体跑调    %+.1f 音分%s'
          % (info.get('offset_cents', 0.0),
             '（已自动校正）' if not args.no_calibrate else '（未校正）'))

    if 'margin_hit' in info or 'margin_drop' in info:
        print('置信度      认出的中位数 %.2f　丢掉的中位数 %.2f'
              % (info.get('margin_hit', float('nan')),
                 info.get('margin_drop', float('nan'))))
    allm = info.get('margin_all')
    if allm:
        n = len(allm)
        print('            全体分位：10%%=%.2f  30%%=%.2f  50%%=%.2f  '
              '70%%=%.2f  90%%=%.2f'
              % (allm[int(n * .1)], allm[int(n * .3)], allm[int(n * .5)],
                 allm[int(n * .7)], allm[min(n - 1, int(n * .9))]))

    if hits:
        import statistics
        cents = [abs(h.cents) for h in hits]
        print('音准        |偏差| 中位数 %.0f 音分，最大 %.0f 音分'
              % (statistics.median(cents), max(cents)))
        names = {}
        for h in hits:
            names[h.pitch] = names.get(h.pitch, 0) + 1
        top = sorted(names.items(), key=lambda kv: -kv[1])
        print('用到的键    ' + '  '.join('%s×%d' % (k, v) for k, v in top))

    if args.debug and hits:
        print('-' * 62)
        for h in hits[:60]:
            print('  %7.3fs  %4s  %8.2f Hz  %+6.1f 音分'
                  % (h.time, h.pitch, h.freq, h.cents))
        if len(hits) > 60:
            print('  … 还有 %d 个' % (len(hits) - 60))

    if not tokens:
        print('-' * 62)
        print('没认出任何音符。可能是：伴奏盖过了琴声 / 不是这台琴 / 静音。')
        return 1

    text, _ = transcribe.to_sheet_text(
        audio, rate, bpm=bpm, snap=args.snap,
        title=os.path.splitext(os.path.basename(args.path))[0])
    print('-' * 62)
    print(text[:400] + ('\n…（后面还有 %d 字）' % max(0, len(text) - 400))
          if len(text) > 400 else text)

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('-' * 62)
        print('已写入 %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
