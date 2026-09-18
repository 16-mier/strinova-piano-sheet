# -*- coding: utf-8 -*-
"""验证「时间轴谱面」格式：`秒:音高`。

用户原话：「这个方案无法向左拖动，你保存音频用的格式需要修改」。

根子在于老格式（`5 3 5`）里每个音的位置是"前一个音 + 时值"**推**出来的，
向左挪就等于要求"负间距" —— 数学上不存在，怎么调都拖不动。
新格式里每个音自带**绝对时间（秒）**，想放哪就放哪，还能互相重叠。

    python tools/test_free_format.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import parser                       # noqa: E402
from core.edit_model import SPB, EditModel    # noqa: E402

TXT = "0:5 0.5:3 1:5 1:1'&3' 2.5:2'"


def secs(m):
    return [round(n.start * SPB, 3) for n in m.notes]


def main() -> int:
    bad = 0
    print('=' * 72)
    print('时间轴谱面格式自测')
    print('=' * 72)

    # ---- 1. 解析 ----
    print('\n[1] 解析 %r' % TXT)
    sh = parser.parse(TXT)
    print('    free = %s   事件 %d 个' % (sh.free, len(sh.events)))
    if not sh.free:
        print('    ❌ 没识别成时间轴格式')
        bad += 1
    else:
        print('    ✅ 识别成时间轴格式')

    m = EditModel(sh)
    got = secs(m)
    print('    位置 = %s' % got)
    print('    音高 = %s' % ['&'.join(n.pitches) for n in m.notes])
    want = [0.0, 0.5, 1.0, 1.0, 2.5]
    if got == want:
        print('    ✅ 每个音都落在它自己写的时间上（含同一时刻的和弦）')
    else:
        print('    ❌ 期望 %s' % want)
        bad += 1

    # ---- 2. 写回 ----
    print('\n[2] 写回文本（rebuild）')
    out = m.rebuild()
    print('    %r' % out)
    sh2 = parser.parse(out)
    m2 = EditModel(sh2)
    if secs(m2) == got:
        print('    ✅ 往返一致（写法变了但时间一个不差）')
    else:
        print('    ❌ 往返后位置变了：%s' % secs(m2))
        bad += 1

    # ---- 3. ★ 向左拖（老格式做不到的事）----
    print('\n[3] ★ 向左拖 ★ 把第 2 个音从 1.0 秒拖到 0.3 秒')
    m3 = EditModel(parser.parse('0:5 1:3 2:5'))
    print('    拖前 %s' % secs(m3))
    m3.move_note_independent(m3.notes[1], 0.3 / SPB)
    after = secs(m3)
    print('    拖后 %s' % after)
    if any(abs(s - 0.3) < 1e-6 for s in after):
        print('    ✅ 向左移动成功（老格式这里必然卡死在原地）')
    else:
        print('    ❌ 没动')
        bad += 1
    if abs(after[0]) < 1e-6 and abs(after[-1] - 2.0) < 1e-6:
        print('    ✅ 其它音原地没动')
    else:
        print('    ❌ 别的音被带动了')
        bad += 1

    # ---- 4. 重叠 ----
    print('\n[4] ★ 重叠 ★ 把第 3 个音也拖到 0.3 秒（和老格式做不到的完全重合）')
    m4 = EditModel(parser.parse('0:5 1:3 2:5'))
    m4.move_note_independent(m4.notes[1], 0.3 / SPB)
    tgt = [n for n in m4.notes if abs(n.start * SPB - 2.0) < 0.01][0]
    m4.move_note_independent(tgt, 0.3 / SPB)
    s4 = secs(m4)
    print('    位置 = %s' % s4)
    if sum(1 for s in s4 if abs(s - 0.3) < 1e-6) == 2:
        print('    ✅ 两个音重合在 0.3 秒（时间轴谱面允许重叠）')
    else:
        print('    ❌ 没重合')
        bad += 1

    # ---- 5. 老写法进来就被摊平 ----
    print('\n[5] 老写法（`5 3 5`）的处理')
    for old in ('1 2 3', '1 - 2 - 3'):
        so = parser.parse(old)
        mo = EditModel(so)
        print('    %-12r free=%-5s 位置(秒)=%s'
              % (old, so.free, secs(mo)))
        if not so.free:
            print('       ❌ 应该统一成时间轴格式')
            bad += 1
    print('    ✅ 老写法只是一种**输入形式** —— 进来就被换算成绝对秒，')
    print('       内部只有一种模型（所以没有"两套规则"要对）')

    # ---- 6. 一键转换：只改写法不改时间 ----
    print('\n[6] `to_free_text()` 导出')
    mc = EditModel(parser.parse('1 2 3 4'))
    before = secs(mc)
    txt2 = mc.to_free_text()
    mc2 = EditModel(parser.parse(txt2))
    after2 = secs(mc2)
    print('    转换前 %s' % before)
    print('    新文本 %r' % txt2)
    print('    转换后 %s' % after2)
    if after2 == before and mc2.free:
        print('    ✅ 只是写法变了，时间一个不差')
    else:
        print('    ❌ 时间变了')
        bad += 1

    # ---- 7. 连按不合并 + 自动分轨 ----
    print('\n[7] ★ 连按三个键：三个独立方块，自动分到三条轨道 ★')
    m7 = EditModel(parser.parse('0:1'))
    base = m7.notes[0].start
    for p in ('3', '5', "2'"):
        m7.add_free_note(base, p)
    print('    方块数 = %d' % len(m7.notes))
    print('    文本 = %r' % m7.rebuild().replace('\n', ' | '))
    if len(m7.notes) == 4:
        print('    ✅ 每次按下都是一个独立方块（没被并成一个和弦）')
    else:
        print('    ❌ 被合并了 —— 用户：「连续按了还是连在一块」')
        bad += 1
    m7.auto_lanes(6, 0.8)
    lanes = [n.lane for n in m7.notes]
    print('    轨道 = %s' % lanes)
    if len(set(lanes)) == len(lanes):
        print('    ✅ 同一时刻的方块各占一条轨道（自动分开）')
    else:
        print('    ❌ 有方块挤在同一条轨道上')
        bad += 1

    print('')
    print('=' * 72)
    print('结果：%s' % ('全部通过' if bad == 0 else '有 %d 项不合格' % bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
