# -*- coding: utf-8 -*-
"""`ui/views.py` 里那部分**纯逻辑**的单元测试。

★ 为什么能测 ★
  "每个格子在第几个音出现"这件事原来是算在 `paintEvent` 里的，
  要验它只能"画一遍再读像素"—— 又脆又难写。
  抽成 `cell_orders()` 之后就是"给一串音符，看返回的字典对不对"。

★ 这一组测试是冲着用户报的那个问题来的 ★
  「同一个按键需要按下两次的时候显示不明显」。
  根因是 `cell_orders` 的前身用了 `setdefault`，同一个格子只记住
  **最早**那次出现的序号 —— `5 5` 在浮窗上只显示一个 `1`。
"""

from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 只要不弹窗就行 —— `cell_orders` 本身跟 Qt 无关，
# 但 import `ui.views` 会连带把 PyQt6 拉进来。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from core import layout                                      # noqa: E402
from ui.views import cell_orders, repeat_run                 # noqa: E402


def group(*pitches) -> list[tuple[list[tuple[int, int]], bool, str]]:
    """把音高序列拼成 `_current_group()` 那种结构。"""
    out = []
    for ps in pitches:
        cell_list = [layout.pitch_to_cell(p) for p in ps]
        cell_list = [c for c in cell_list if c is not None]
        out.append((cell_list, False, '+'.join(ps)))
    return out


def test_single_note_each_cell_appears_once():
    orders = cell_orders(group(('5',), ('3',), ('7',)))
    assert orders[layout.pitch_to_cell('5')] == [0]
    assert orders[layout.pitch_to_cell('3')] == [1]
    assert orders[layout.pitch_to_cell('7')] == [2]


def test_repeated_key_records_every_occurrence():
    """★ 这条就是用户报的那个场景 ★

    `5 5 5` —— 同一个键连按三下，必须记成 [0, 1, 2]。
    修之前只会得到 [0]（`setdefault` 的锅），
    于是浮窗上只看到一个 "1"，第二、第三下完全看不出来。
    """
    cell5 = layout.pitch_to_cell('5')
    orders = cell_orders(group(('5',), ('5',), ('5',)))
    assert orders[cell5] == [0, 1, 2]


def test_repeated_key_not_adjacent():
    """隔开的重复也要记全（`5 3 5`）。"""
    cell5 = layout.pitch_to_cell('5')
    orders = cell_orders(group(('5',), ('3',), ('5',)))
    assert orders[cell5] == [0, 2]


def test_chord_members_share_the_same_rank():
    """一个和弦里的几个键属于**同一个**序号（一起按）。"""
    orders = cell_orders(group(('1', '3', '5'), ('7',)))
    for p in ('1', '3', '5'):
        assert orders[layout.pitch_to_cell(p)] == [0]
    assert orders[layout.pitch_to_cell('7')] == [1]


def test_chord_plus_repeat():
    """和弦里的一员在下一个音又单独出现一次。"""
    cell5 = layout.pitch_to_cell('5')
    orders = cell_orders(group(('1', '5'), ('5',)))
    assert orders[cell5] == [0, 1]
    assert orders[layout.pitch_to_cell('1')] == [0]


def test_empty_group():
    assert cell_orders([]) == {}


def test_unplayable_pitch_is_ignored():
    """琴上没有的音（`layout.pitch_to_cell` 返回 None）不该让函数崩。

    ★ 注意序号会**跳** ★
      序号是"这是预览里的第几个音"，不是"第几个格子"。
      中间那个 `9` 在琴上没有键，所以它不占格子 —— 但它**确实是一个音**，
      于是后面那个 `5` 的序号是 2 而不是 1。
      这是有意的：序号要跟玩家看到的"谱面上第几个音"对得上。
    """
    orders = cell_orders(group(('5',), ('9',), ('5',)))
    assert orders[layout.pitch_to_cell('5')] == [0, 2]


def test_ranks_are_ascending():
    """同一个格子的序号必须是递增的 —— 画角标时按顺序拼 `1·3`。"""
    orders = cell_orders(group(('5',), ('3',), ('5',), ('3',), ('5',)))
    for _cell, ranks in orders.items():
        assert ranks == sorted(ranks)
    assert orders[layout.pitch_to_cell('5')] == [0, 2, 4]
    assert orders[layout.pitch_to_cell('3')] == [1, 3]


# ----------------------------------------------------------------------
# 连按次数（用户要的 `×2`）
# ----------------------------------------------------------------------

def test_repeat_run_counts_consecutive():
    """★ 用户：「如果是连续点俩下在 1 下面写一个 X2」★

    数的是**相邻连续**，不是"总共出现几次"。
    """
    cell5 = layout.pitch_to_cell('5')
    # 5 5 5 —— 从第 0 个起连着 3 个
    assert repeat_run(group(('5',), ('5',), ('5',)), 0, cell5) == 3
    # 5 3 5 —— 从第 0 个起只有 1 个（中间隔着 3，不用连按）
    assert repeat_run(group(('5',), ('3',), ('5',)), 0, cell5) == 1
    # 3 5 5 —— 从第 1 个起连着 2 个
    assert repeat_run(group(('3',), ('5',), ('5',)), 1, cell5) == 2


def test_repeat_run_stops_at_first_other_note():
    """5 5 3 5：从 0 起是 2（前两个），从 1 起是 1。"""
    cell5 = layout.pitch_to_cell('5')
    g = group(('5',), ('5',), ('3',), ('5',))
    assert repeat_run(g, 0, cell5) == 2
    assert repeat_run(g, 1, cell5) == 1
    assert repeat_run(g, 3, cell5) == 1


def test_repeat_run_counts_chord_containing_the_key():
    """和弦里只要**含这个键**就算还得按它（`5 5&3 5` 里 5 要按三下）。"""
    cell5 = layout.pitch_to_cell('5')
    g = group(('5',), ('5', '3'), ('5',))
    assert repeat_run(g, 0, cell5) == 3


def test_repeat_run_breaks_on_rest():
    """休止符要中断连续 —— 那段是真的没声音。"""
    cell5 = layout.pitch_to_cell('5')
    out = group(('5',), ('5',))
    # 手工插一个休止
    out.append(([], True, '休止'))
    out.append(([cell5], False, '5'))
    assert repeat_run(out, 0, cell5) == 2


def test_repeat_run_from_out_of_range():
    cell5 = layout.pitch_to_cell('5')
    assert repeat_run(group(('5',)), 5, cell5) == 0


def test_current_cell_counts_only_what_is_left():
    """★ 用户：「在切换到需要点俩下的按键上已经点一下了就要把 ×2 去掉」★

    当前这个音**正在按**，所以它不该算进"还要按几下" ——
    当前格上的 `×N` 要用 `repeat_run(group, 0, …) - 1`。

    这跟"下一个格"上用 `repeat_run(group, 1, …)` 是同一个语义：
    **`×N` = 从这个标记所在的音起，还要按 N 下。**
    """
    cell5 = layout.pitch_to_cell('5')
    # 5 5 —— 当前是第一个：从下一个起只剩 1 个 → **不标**（×2 该消失）
    assert repeat_run(group(('5',), ('5',)), 0, cell5) - 1 == 1
    # 5 5 5 —— 从下一个起还有 2 个 → 标 ×2
    assert repeat_run(group(('5',), ('5',), ('5',)), 0, cell5) - 1 == 2
    # 5 5 5 5 5 —— 从下一个起还有 4 个 → ×4
    assert repeat_run(group(*[('5',)] * 5), 0, cell5) - 1 == 4


def test_walking_forward_clears_the_marker():
    """时钟往前走一格，原来那个 `×2` 就该没了。

    `5 5 3 5`：
      · 当前是第 1 个 5 → 从下一个起连着 1 个 → 不标
      · 当前是第 2 个 5 → 从下一个起是 3 → 不标
      · 当前是那个 3   → 从下一个起（5）连着 1 个 → 不标
    只有 `5 5` 连在一起且**后面还有**时才会看到 ×N。
    """
    cell5 = layout.pitch_to_cell('5')
    g = group(('5',), ('5',), ('3',), ('5',))
    assert repeat_run(g, 0, cell5) - 1 == 1     # 第 1 个 5 处：不标
    assert repeat_run(g, 1, cell5) - 1 == 0     # 第 2 个 5 处：不标
    assert repeat_run(g, 3, cell5) - 1 == 0     # 最后一个 5：不标


def test_three_in_a_row_keeps_marker_until_the_last_one():
    """`5 5 5`：第 1 个 5 处标 ×2，第 2 个 5 处就只剩 1 了 → 不标。"""
    cell5 = layout.pitch_to_cell('5')
    g = group(('5',), ('5',), ('5',))
    assert repeat_run(g, 0, cell5) - 1 == 2     # ×2
    assert repeat_run(g, 1, cell5) - 1 == 1     # 不标
    assert repeat_run(g, 2, cell5) - 1 == 0     # 不标


# ----------------------------------------------------------------------
# ★ 「正在播放的按键闪烁」★
#
#   用户：「**为什么要按照声音判断，不是按写好了的铺子判断吗，
#   然后正在播放的按键闪烁**，这个还是存在延迟俩个按键的问题」。
#
#   这里钉住的就是那句话：闪烁必须**纯谱面驱动** ——
#   谱面时间跨过某个音的那一刻就闪它，中间不经过音频、不经过识别。
#
#   之前那版是靠**听到声音**才闪（`_on_onset` → `flash_note`），
#   闪的动作于是背着整条音频链路的延迟 ——
#   用户看到的「闪烁的键落后两个」就是这么来的。
# ----------------------------------------------------------------------

class _Note:
    """`timeline_from_notes()` 要的最小音符形状。"""

    def __init__(self, start, pitch):
        self.start = float(start)
        self.pitches = [pitch]
        self.is_rest = False
        self.label = pitch


@pytest.fixture(scope='module')
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def view(qapp):
    """一个装着 3 个音（0.0 / 0.5 / 1.0 秒）的 GridView。"""
    from core.timeline import timeline_from_notes
    from ui.views import GridView
    v = GridView()
    v.set_timeline(timeline_from_notes([_Note(0.0, '1'),
                                        _Note(0.5, '2'),
                                        _Note(1.0, '3')]))
    v.mark_hold = 0.06          # 测试里没必要等 0.18 秒
    return v


def test_flash_happens_on_note_change(view):
    """谱面走到一个音 → 那个格要闪。"""
    view.set_time(0.0)
    assert view.current_blinking(), '第一个音就该闪'


def test_flash_works_with_always_on_marker(view):
    """★ 关键一条：`mark_current=True`（跟谱面模式）时**照样**要闪 ★

    原来那段写着 `if self.mark_current: return False` ——
    也就是"当前格常亮着的时候就不闪了"。
    可用户要的恰恰是两者同时成立：

        常亮（深色打底）= 「接下来该弹这个」
        换音瞬间闪一下 = 「就是现在」

    一个是底色、一个是节拍提示，本来就不冲突。
    """
    view.mark_current = True
    view.set_time(0.0)
    assert view.current_blinking(), '常亮模式下不闪了 —— 用户要的正是这个'


def test_flash_only_on_note_change_not_every_tick(view):
    """同一个音里时钟走多少帧都不该再闪 —— 否则会一直闪个不停。

    时钟是 8 ms 一跳，一个音可能跨十几跳。
    靠 `_cur_stamp` 的"内容指纹"来判：只有**换音**才重新计时。
    """
    view.set_time(0.0)
    assert view.current_blinking()
    time.sleep(view.mark_hold + 0.03)
    assert not view.current_blinking()
    view.set_time(0.1)                       # 还在第 1 个音（0.0~0.5）
    assert not view.current_blinking(), '同一个音内不该重新闪'
    view.set_time(0.3)
    assert not view.current_blinking()
    view.set_time(0.5)                       # 换到第 2 个音了
    assert view.current_blinking(), '换音了却没闪'


def test_flash_expires(view):
    """闪一下就灭 —— `has_flash()` 靠它决定还要不要继续重绘。

    不灭的话那个 8 ms 的 `_flash_tick` 会一直空转。
    """
    view.set_time(0.0)
    assert view.has_flash()
    time.sleep(view.mark_hold + 0.03)
    assert not view.has_flash()


def test_no_flash_before_start(view):
    """还没开始走的时候不该凭空闪。"""
    assert not view.current_blinking()
    assert not view.has_flash()
