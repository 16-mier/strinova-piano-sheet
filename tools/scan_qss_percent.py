# -*- coding: utf-8 -*-
"""扫描 QSS 模板里的**孤立百分号**。

用法：python tools/scan_qss_percent.py

★ 为什么需要它 ★
  整张样式表是「三引号模板 百分号 dict(...)」格式化出来的。注释里随手
  写一个百分号（比如 CSS 里那句常见的"宽度撑满"），Python 会把它当成
  格式符，`build_qss()` 当场抛 "unsupported format character"。

  而 `apply()` 是**故意**吞异常的（见那边的注释：主题装不上也不该把
  程序带走），所以症状是"界面悄悄退回系统默认配色"，一个字都不报。
  这个脚本就是来把这个静默兜住的。

  合法形式只有 `%(NAME)s` 一种，其余全是雷。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, 'ui', 'appstyle.py')

# 只有 `%(Name)s` 是合法的
LEGAL = re.compile(r'%(?!\([A-Za-z_]+\)s)')


def main() -> int:
    with open(TARGET, encoding='utf-8') as f:
        lines = f.read().split('\n')

    start = next(i for i, l in enumerate(lines) if '    return """' in l)
    end = next(i for i, l in enumerate(lines) if l.startswith('""" % dict('))
    tmpl = '\n'.join(lines[start:end])

    print('模板区 = 第 %d ~ %d 行' % (start + 1, end + 1))

    bad = 0
    for m in LEGAL.finditer(tmpl):
        ln = start + 1 + tmpl[:m.start()].count('\n')
        ctx = tmpl[max(0, m.start() - 46):m.start() + 26].replace('\n', ' / ')
        print('  ★ 第 %d 行：…%s…' % (ln, ctx))
        bad += 1

    if bad:
        print()
        print('【失败】%d 个孤立百分号 —— `build_qss()` 会抛异常' % bad)
        print('         （要写字面百分号，写两个：百分号百分号）')
        return 1
    print('【通过】模板里只有 `%(NAME)s` 一种百分号用法')
    return 0


if __name__ == '__main__':
    sys.exit(main())
