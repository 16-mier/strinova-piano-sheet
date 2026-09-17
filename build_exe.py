# -*- coding: utf-8 -*-
"""打包成 exe。

用法：python build_exe.py
产物：dist/卡丘琴谱器/卡丘琴谱器.exe

注意：PyInstaller 收尾时偶尔报 exit code 1（资源占用重试），
只要 dist 下的目录生成了就算成功 —— 检查产物是否存在。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = '卡丘琴谱器'


def main() -> int:
    for d in ('build', 'dist'):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    args = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean', '--windowed',
        '--name', NAME,
        '--add-data', 'sheets;sheets',
        os.path.join(ROOT, 'main.py'),
    ]
    subprocess.run(args, cwd=ROOT, check=False)

    exe = os.path.join(ROOT, 'dist', NAME, NAME + '.exe')
    if os.path.isfile(exe):
        print('Build complete! -> %s (%d bytes)' % (exe, os.path.getsize(exe)))
        return 0
    print('构建失败：没找到 exe')
    return 1


if __name__ == '__main__':
    sys.exit(main())
