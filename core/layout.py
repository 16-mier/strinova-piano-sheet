# -*- coding: utf-8 -*-
"""游戏里那台琴的 16 个键位 —— 布局与音高映射。

排列（跟游戏画面一致，行 0 是最下面一行）：

    行3（上） PAD13(5')  PAD14(6')  PAD15(7')  PAD16(1'')
    行2       PAD9 (1')  PAD10(2')  PAD11(3')  PAD12(4')
    行1       PAD5 (5)   PAD6 (6)   PAD7 (7)   PAD8 (8)
    行0（下） PAD1 (1)   PAD2 (2)   PAD3 (3)   PAD4 (4)

PAD 编号 = 行号 × 4 + 列号 + 1。

注意：`8` 和 `1'` 在乐理上是同一个音高（高音 do），游戏里却是两个独立的键。
本程序按**标签精确匹配** —— 谱面写 `8` 就指向 PAD8，写 `1'` 就指向 PAD9。
若实际打下来发现对不上，改这张表即可（就是下面这个 PAD_GRID）。
"""

from __future__ import annotations

PAD_ROWS = 4
PAD_COLS = 4
PAD_COUNT = PAD_ROWS * PAD_COLS

# ★ 唯一的真相来源：改这里就能改键位映射 ★
PAD_GRID: list[list[str]] = [
    ['1', '2', '3', '4'],            # 行 0（最下）
    ['5', '6', '7', '8'],            # 行 1
    ["1'", "2'", "3'", "4'"],        # 行 2
    ["5'", "6'", "7'", "1''"],       # 行 3（最上）
]

# ★ 游戏里每个键面上印的是 "PAD N"，不是简谱音名 ★
#   浮窗上两个都标：音名（谱面用）+ PAD 号（游戏里对着找键用）。
PAD_LABELS: list[list[str]] = [
    ['PAD %d' % (r * PAD_COLS + c + 1) for c in range(PAD_COLS)]
    for r in range(PAD_ROWS)
]


def pad_number(row: int, col: int) -> int:
    """格子坐标 -> 游戏里的 PAD 编号（1~16）。"""
    return row * PAD_COLS + col + 1


def cell_to_pitch(row: int, col: int) -> str:
    """格子坐标 -> 音高标签。"""
    return PAD_GRID[row][col]


def pitch_to_cell(pitch: str) -> tuple[int, int] | None:
    """音高标签 -> (行, 列)。琴上没这个音就返回 None。"""
    for r in range(PAD_ROWS):
        for c in range(PAD_COLS):
            if PAD_GRID[r][c] == pitch:
                return (r, c)
    return None


def pitch_to_pad(pitch: str) -> int | None:
    """音高标签 -> PAD 编号（1~16）。找不到返回 None。"""
    cell = pitch_to_cell(pitch)
    if cell is None:
        return None
    return pad_number(cell[0], cell[1])


def all_pitches() -> list[str]:
    """琴上所有音高，按 PAD 编号顺序。"""
    return [cell_to_pitch(r, c)
            for r in range(PAD_ROWS) for c in range(PAD_COLS)]


def unmapped_pitches(pitches) -> list[str]:
    """在一堆音高里，找出琴上弹不出来的那些（去重保序）。"""
    seen = set()
    out = []
    for p in pitches:
        if p not in seen and pitch_to_cell(p) is None:
            seen.add(p)
            out.append(p)
    return out
