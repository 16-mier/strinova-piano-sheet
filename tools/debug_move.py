# -*- coding: utf-8 -*-
"""诊断 move_note_independent：拖一个音，后面的到底动没动、为什么动。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import parser                       # noqa: E402
from core.edit_model import EditModel         # noqa: E402

SHEET = '1 - 2 - 3 - 4'


def dump(m, tag):
    print('--- %s ---' % tag)
    for i, n in enumerate(m.notes):
        print('  [%d] start=%.4f dur=%.4f %s raw=%r'
              % (i, n.start, n.dur, '休止' if n.is_rest else '音', n.raw))
    print('  rebuild: %r' % m.rebuild())


m = EditModel(parser.parse(SHEET))
dump(m, '初始')

ns = [n for n in m.notes if not n.is_rest]
tgt = ns[1]
print('\n拖 音2（start=%.2f）到 2.5' % tgt.start)
print('  移动量 = %.4f' % m.move_note_independent(tgt, 2.5))
dump(m, '拖完')

print('\n期望：音1=0.0 音2=2.5 音3=4.0 音4=6.0')
