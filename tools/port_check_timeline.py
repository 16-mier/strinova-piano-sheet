# -*- coding: utf-8 -*-
"""对拍：`core/timeline.py` 和 `web/js/timeline.js` 必须算出一模一样的时间轴。

比 parser 那份更要紧 —— 时间轴决定"这一刻该弹哪个键"。
差一点点，浮窗上显示的当前格就是错的（而且看起来"像是能用"）。

比的东西：每个音的位置/时长/BPM、总时长、bpm_points、
以及在若干个时间点上问出来的 index_at / upcoming / bpm_at。
"""
import json
import os
import subprocess
import sys

ROOT = r"C:\Users\mier\Desktop\deepseek work\strinova-piano-sheet"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import parser as P                                    # noqa: E402
from core.timeline import Timeline, timeline_from_notes         # noqa: E402

SHEETS = {
    '基本序列': '小星星\n#120#\n1 1 5 5 6 6 5-',
    '和弦': '#120#\n1&3&5 2&4 3',
    '变速': '#60# 1 2 #120# 3 4 #180# 5',
    '时间轴格式': "#120#\n0:5 0.25:3 1:1'&3' 2.5:2'",
    '时间轴重叠': '#120#\n0:1 0:2 0.5:3 0.5:4',
    '负时间起手': '#120#\n-1:5 0:6 1:7',
    '单音': '#120#\n1',
    '空谱': '',
    '休止': '#120#\n1 - 2 -- 3',
    '零点五秒粒度': '#120#\n0:1 0.5:2 1:3 1.5:4 2:5',
}

NOTES = {
    '三个音': [
        {'start': 0.0, 'pitches': ['1'], 'label': '1'},
        {'start': 0.5, 'pitches': ['3'], 'label': '3'},
        {'start': 1.25, 'pitches': ['5'], 'label': '5'},
    ],
    '带休止和和弦': [
        {'start': 0.0, 'pitches': ['1', '3'], 'label': '1&3'},
        {'start': 1.0, 'pitches': [], 'is_rest': True},
        {'start': 2.0, 'pitches': ['5'], 'label': '5'},
    ],
    '空列表': [],
}

# 在这些时间点上问 index_at / upcoming / bpm_at
PROBES = [-5.0, -0.5, 0.0, 0.1, 0.24, 0.25, 0.26, 0.5, 0.75,
          1.0, 1.24, 1.25, 1.5, 2.0, 2.5, 3.0, 10.0, 100.0]


def py_sheet_result(text):
    tl = Timeline(P.parse(text))
    return {
        'total_sec': tl.total_sec,
        'total_beats': tl.total_beats,
        'bpm_points': [list(bp) for bp in tl.bpm_points],
        'items': [
            {'index': it.index, 'start_beat': it.start_beat,
             'start_sec': it.start_sec, 'end_sec': it.end_sec,
             'bpm': it.bpm, 'duration_sec': it.duration_sec,
             'pitches': list(it.chord.pitches), 'is_rest': it.chord.is_rest,
             'raw': it.chord.raw}
            for it in tl.items
        ],
        'probes': {
            # ★ 键名要两边写法一致 ★
            #   第一版 Python 用 `str(s)`、JS 用 `String(s)` —— 于是
            #   `-5.0` 对上 `-5`、`0.0` 对上 `0`，满屏"只在一边有"，
            #   看着像代码不一致，其实是**对拍工具自己**没对齐。
            #   固定成三位小数两边就一样了。
            '%.3f' % s: {
                'index_at': tl.index_at(s),
                'upcoming': [it.index for it in tl.upcoming(s, 3)],
                'bpm_at': tl.bpm_at(s),
                'spb': tl.seconds_per_beat(s),
            } for s in PROBES
        },
        'stats': tl.stats(),
        'all_pitches': tl.all_pitches(),
    }


def py_notes_result(notes, bpm=120):
    class N:
        def __init__(self, d):
            self.start = d.get('start', 0.0)
            self.pitches = list(d.get('pitches', []))
            self.is_rest = d.get('is_rest', False)
            self.label = d.get('label')

    tl = timeline_from_notes([N(d) for d in notes], bpm)
    return {
        'total_sec': tl.total_sec,
        'count': len(tl.items),
        'items': [
            {'start_sec': it.start_sec, 'end_sec': it.end_sec,
             'pitches': list(it.chord.pitches), 'raw': it.chord.raw,
             'bpm': it.bpm}
            for it in tl.items
        ],
    }


NODE_SCRIPT = """
import fs from 'fs';
import { parse } from '../web/js/parser.js';
import { Timeline, timeline_from_notes } from '../web/js/timeline.js';

const inp = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const PROBES = inp.probes;

const out = { sheets: {}, notes: {} };

for (const [name, text] of Object.entries(inp.sheets)) {
  const tl = new Timeline(parse(text));
  const probes = {};
  for (const s of PROBES) {
    probes[s.toFixed(3)] = {
      index_at: tl.index_at(s),
      upcoming: tl.upcoming(s, 3).map(it => it.index),
      bpm_at: tl.bpm_at(s),
      spb: tl.seconds_per_beat(s),
    };
  }
  out.sheets[name] = {
    total_sec: tl.total_sec,
    total_beats: tl.total_beats,
    bpm_points: tl.bpm_points.map(bp => [bp[0], bp[1]]),
    items: tl.items.map(it => ({
      index: it.index, start_beat: it.start_beat,
      start_sec: it.start_sec, end_sec: it.end_sec,
      bpm: it.bpm, duration_sec: it.duration_sec,
      pitches: it.chord.pitches, is_rest: it.chord.is_rest,
      raw: it.chord.raw,
    })),
    probes,
    stats: tl.stats(),
    all_pitches: tl.all_pitches(),
  };
}

for (const [name, notes] of Object.entries(inp.notes)) {
  const tl = timeline_from_notes(notes, 120);
  out.notes[name] = {
    total_sec: tl.total_sec,
    count: tl.items.length,
    items: tl.items.map(it => ({
      start_sec: it.start_sec, end_sec: it.end_sec,
      pitches: it.chord.pitches, raw: it.chord.raw, bpm: it.bpm,
    })),
  };
}

fs.writeFileSync(process.argv[3], JSON.stringify(out));
"""


def close(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append('%s.%s 只在 JS 里有' % (path, k))
            elif k not in b:
                out.append('%s.%s 只在 Python 里有' % (path, k))
            else:
                diff('%s.%s' % (path, k), a[k], b[k], out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append('%s 长度不同: py=%d js=%d' % (path, len(a), len(b)))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff('%s[%d]' % (path, i), x, y, out)
    elif not close(a, b):
        out.append('%s 不同: py=%r js=%r' % (path, a, b))


def main():
    tmp = os.path.join(ROOT, '_port_check')
    os.makedirs(tmp, exist_ok=True)
    inp_p = os.path.join(tmp, 'tl_in.json')
    js_p = os.path.join(tmp, 'tl_js.json')
    node_p = os.path.join(tmp, 'tl_run.mjs')

    with open(inp_p, 'w', encoding='utf-8') as f:
        json.dump({'sheets': SHEETS, 'notes': NOTES, 'probes': PROBES},
                  f, ensure_ascii=False)
    with open(node_p, 'w', encoding='utf-8') as f:
        f.write(NODE_SCRIPT)

    r = subprocess.run(['node', node_p, inp_p, js_p], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        print('node 跑失败：')
        print(r.stdout)
        print(r.stderr)
        return 1

    with open(js_p, encoding='utf-8') as f:
        js = json.load(f)

    bad = 0
    print('  %-16s %-8s %s' % ('用例', '事件数', '结果'))
    print('  ' + '-' * 52)

    for name, text in SHEETS.items():
        py = py_sheet_result(text)
        j = js['sheets'].get(name)
        d = []
        diff(name, py, j, d)
        if d:
            bad += 1
            print('  %-16s %-8d ★ %d 处不同' % (name, len(py['items']), len(d)))
            for line in d[:6]:
                print('        ' + line)
        else:
            print('  %-16s %-8d 一致' % (name, len(py['items'])))

    for name, notes in NOTES.items():
        py = py_notes_result(notes)
        j = js['notes'].get(name)
        d = []
        diff('notes:' + name, py, j, d)
        if d:
            bad += 1
            print('  %-16s %-8d ★ %d 处不同' % ('notes:' + name, py['count'], len(d)))
            for line in d[:6]:
                print('        ' + line)
        else:
            print('  %-16s %-8d 一致' % ('notes:' + name, py['count']))

    print()
    n = len(SHEETS) + len(NOTES)
    if bad:
        print('【失败】%d / %d 个用例对不上' % (bad, n))
        return 1
    print('【通过】%d 个用例、%d 个时间探针，Python 和 JS 时间轴完全一致'
          % (n, len(PROBES)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
