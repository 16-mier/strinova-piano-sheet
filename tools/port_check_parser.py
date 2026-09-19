# -*- coding: utf-8 -*-
"""对拍：`core/parser.py`（Python）和 `web/js/parser.js`（JS）必须解析出同样的结果。

★ 为什么非要这么较真 ★
  Python 原版**还在用**（桌面版），JS 版是新写的。两边解析同一份谱面文本，
  只要有一处不一样，用户的谱面在手机上和在电脑上就会长得不同 ——
  而这种 bug 极难发现（"手机上看着好好的"）。

  所以：同一批用例分别喂给两边，把事件流打成 JSON **逐字段**比。

用例覆盖：正常谱面、和弦、延长/减半、前缀脱字符、多段变速、
时间轴格式（`秒:音高`）、非法 token、中文混排、CRLF、空文本、
零/超大 BPM、只有冒号…… 都是真实会遇到的写法。
"""
import json
import os
import subprocess
import sys

ROOT = r"C:\Users\mier\Desktop\deepseek work\strinova-piano-sheet"
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import parser as P                                    # noqa: E402

CASES = {
    '空文本': '',
    '只有标题': '小星星',
    '标题+BPM': '小星星\n#120#',
    '基本序列': '小星星\n#120#\n1 1 5 5 6 6 5-',
    '和弦': '#120#\n1&3&5 2&4',
    '延长与减半': '#120#\n1- 2 3^ 4^^',
    '前缀脱字符': '#120#\n^1 ^2 1',
    '多段变速': '#60# 1 #120# 1 #180# 1',
    '时间轴格式': "#120#\n0:5 0.25:3 1:1'&3' 2.5:2'",
    '时间轴小数': '#120#\n0.5:1 1.25:2 10:3',
    '非法 token': '#120#\n1 abc 2 @@@ 3',
    '非乐谱文字': '#120#\n鼓点 1 2',
    '中文备注混排': '小星星\n#120#\n1 2 3\n这是备注\n4 5',
    '空拍与休止': '#120#\n1 - 2',
    '多行': '#120#\n1 2 3\n4 5 6',
    '制表符分隔': '#120#\n1\t2\t3',
    'CRLF': '#120#\r\n1 2 3\r\n4 5',
    '零 BPM': '#0#\n1 2',
    '超大 BPM': '#999#\n1 2',
    '冒号但非数字': '#120#\nabc:1 x:2',
    '只有冒号': '#120#\n:1 2',
    '双撇音高': "#120#\n1'' 2' 3",
    '重复 BPM 同点': '#120#\n#120#\n1 2',
    '负时间': '#120#\n-1:5 0:6',
    '尾部空白': '#120#\n1 2 3   \n\n',
}


def py_events(text):
    sheet = P.parse(text)
    out = {'title': sheet.title, 'free': sheet.free, 'events': []}
    for e in sheet.events:
        if isinstance(e, P.BpmChange):
            out['events'].append({'k': 'bpm', 'bpm': e.bpm,
                                  'line': e.line, 'col': e.col})
        else:
            out['events'].append({
                'k': 'chord', 'pitches': list(e.pitches),
                'duration': e.duration, 'is_rest': e.is_rest,
                'raw': e.raw, 'line': e.line, 'col': e.col,
                'at': e.at,
            })
    return out


NODE_SCRIPT = """
import fs from 'fs';
import { parse, Chord, BpmChange } from '../web/js/parser.js';

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = {};
for (const [name, text] of Object.entries(cases)) {
  const sheet = parse(text);
  const events = [];
  for (const e of sheet.events) {
    if (e instanceof BpmChange) {
      events.push({ k: 'bpm', bpm: e.bpm, line: e.line, col: e.col });
    } else {
      events.push({
        k: 'chord', pitches: e.pitches, duration: e.duration,
        is_rest: e.is_rest, raw: e.raw, line: e.line, col: e.col, at: e.at,
      });
    }
  }
  out[name] = { title: sheet.title, free: sheet.free, events };
}
fs.writeFileSync(process.argv[3], JSON.stringify(out));
"""


def close(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a == b
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return a == b
    return a == b


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
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
    cases_p = os.path.join(tmp, 'cases.json')
    js_p = os.path.join(tmp, 'js_out.json')
    node_p = os.path.join(tmp, 'run.mjs')

    with open(cases_p, 'w', encoding='utf-8') as f:
        json.dump(CASES, f, ensure_ascii=False)
    with open(node_p, 'w', encoding='utf-8') as f:
        f.write(NODE_SCRIPT)

    r = subprocess.run(['node', node_p, cases_p, js_p], cwd=ROOT,
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
    print('  ' + '-' * 46)
    for name in CASES:
        py = py_events(CASES[name])
        j = js.get(name)
        if j is None:
            print('  %-16s ——       ★ JS 没输出' % name)
            bad += 1
            continue
        d = []
        diff(name, py, j, d)
        if d:
            bad += 1
            print('  %-16s %-8d ★ %d 处不同' % (name, len(py['events']), len(d)))
            for line in d[:5]:
                print('        ' + line)
        else:
            print('  %-16s %-8d 一致' % (name, len(py['events'])))

    print()
    if bad:
        print('【失败】%d / %d 个用例对不上' % (bad, len(CASES)))
        return 1
    print('【通过】%d 个用例，Python 和 JS 解析结果逐字段一致' % len(CASES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
