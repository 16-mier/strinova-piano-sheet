# -*- coding: utf-8 -*-
"""路径工具 —— 保证打包成 exe 之后也能找对目录。"""

from __future__ import annotations

import os
import sys

APP_NAME = '卡丘琴谱器'


def app_dir() -> str:
    """程序所在目录（源码运行 = 项目根；打包后 = exe 所在目录）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_dir() -> str:
    """内置资源目录。源码运行时 = 项目根；打包后 = PyInstaller 解出来的目录。"""
    return getattr(sys, '_MEIPASS', None) or app_dir()


def sheets_dir() -> str:
    """用户谱面目录 —— 就放在程序旁边，方便自己往里丢曲子。"""
    d = os.path.join(app_dir(), 'sheets')
    os.makedirs(d, exist_ok=True)
    return d


def all_sheets() -> list[str]:
    """所有能看到的谱面：用户目录（优先） + 内置示例。"""
    seen: set[str] = set()
    out: list[str] = []
    for d in (sheets_dir(), os.path.join(bundled_dir(), 'sheets')):
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if not n.lower().endswith('.txt'):
                continue
            p = os.path.join(d, n)
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def config_path() -> str:
    return os.path.join(app_dir(), 'config.json')
