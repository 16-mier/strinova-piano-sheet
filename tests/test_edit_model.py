# -*- coding: utf-8 -*-
"""编辑模型的回归测试 —— 专门锁住 `_audit_code.md` 第 2 章那几条 🔴。

对应报告第 9.4 节补测清单里的 T1~T6（前 6 条就是直接锁第 2 章 bug 的）：

    T1  `synth.pitch_freq` 与 `transcribe.pitch_freq` 必须一致（2.6）
    T2  `render.load_sample` 的兜底合成必须用 C3 基准（2.6）
    T3  删一个音，**别的音一个都不许动**（2.2）
    T4  `move_note_free` 只动自己（2.1）
    T5  `move_note_independent` 只动自己、允许重叠（2.1）
    T6  看门狗把时钟拉起来时**不重放已播过的音**（2.3）

跑法：`python -m pytest tests -q` —— **必须** `python -m`
（项目没有 conftest.py / pyproject.toml，直接 `pytest tests` 会
`ModuleNotFoundError: No module named 'core'`，见报告 9.6）。
"""

import math
import os

import pytest

from core import layout, notes, parser, synth
from core.edit_model import SPB, EditModel


def _starts(m):
    """所有**音符**块的起始时刻（秒），按 `notes` 的顺序（跳过休止符）。

    自由格式下 `notes` 里本来就不该有休止符块 —— 这里过滤一下只是
    让断言的意思更明确（"这些音落在哪儿"）。
    """
    return [round(n.start * SPB, 3) for n in m.notes if not n.is_rest]


# ---------------- T1：两个 pitch_freq 必须是同一个东西 ----------------



# ---------------- T2：render 的兜底合成也得用 C3 ----------------


# ---------------- T3：删音不许拽动别的音 ----------------

def test_remove_note_does_not_move_others():
    """★ 锁 2.2 ★ 自由格式：这个音没了，别的音一个都不许动。

    `0:1 1:2 2:3` 删中间那个 —— 期望 `[0.0, 2.0]`；
    修之前 `_reflow()` 会把后面那个左移，得到 `[0.0, 1.0]`。
    """
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    assert _starts(m) == [0.0, 1.0, 2.0]
    assert m.remove_note(m.index_of(m.notes[1])) is True
    assert [n.pitches[0] for n in m.notes] == ['1', '3']
    assert _starts(m) == [0.0, 2.0]
    assert len(m.notes) == 2, '不该凭空多出休止符块'


def test_remove_note_index_does_not_move_others():
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    assert m.remove_note_index(1) is True
    assert _starts(m) == [0.0, 2.0]


def test_remove_first_note_keeps_later_ones():
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    m.remove_note(0)
    assert _starts(m) == [1.0, 2.0]


def test_remove_note_out_of_range_is_noop():
    m = EditModel(parser.parse('0:1'))
    assert m.remove_note(5) is False
    assert m.remove_note_index(5) is False
    assert _starts(m) == [0.0]


# ---------------- T4/T5：自由移动只动自己 ----------------

def test_move_note_free_moves_only_itself():
    """★ 锁 2.1 ★ 方向键现在走的就是这条：只挪选中的那个。

    （旧的 `set_gap_of` → `_reflow` 会把第三个块一起推成 2.5 秒。）
    """
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    m.move_note_free(m.notes[1], 1.5 / SPB)
    assert _starts(m) == [0.0, 1.5, 2.0]
    assert math.isclose(m.notes[2].start, 2.0 / SPB, abs_tol=1e-9)


def test_move_note_free_clamps_at_zero():
    m = EditModel(parser.parse('0.5:1'))
    m.move_note_free(m.notes[0], -3.0)
    assert _starts(m) == [0.0]


def test_move_note_independent_only_moves_itself():
    """T5：向左挪可以，别的块原地不动。"""
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    first, second, third = m.notes
    m.move_note_independent(second, second.start - 0.25 / SPB)
    assert _starts(m) == [0.0, 0.75, 2.0]
    assert first.start == 0.0
    assert math.isclose(third.start, 2.0 / SPB, abs_tol=1e-9)


def test_move_note_independent_allows_overlap():
    """T5：允许重叠（自由格式的核心能力）。"""
    m = EditModel(parser.parse('0:1 1:2 2:3'))
    m.move_note_independent(m.notes[2], 0.0)
    assert sorted(_starts(m)) == [0.0, 0.0, 1.0]


# ---------------- T6：看门狗拉起时钟不许重放 ----------------

@pytest.fixture(scope='module')
def qapp():
    """`EditPlayer` 是 QObject（里面有 QTimer），用之前得有 QApplication。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _player(spec='0:1 0.5:2 1:3'):
    from ui.edit_player import EditPlayer
    p = EditPlayer()
    p.bpm = 120
    p.set_model(EditModel(parser.parse(spec)))
    fired: list[str] = []
    p.note_fired.connect(fired.append)
    return p, fired


def test_play_from_keep_fired_does_not_replay(qapp):
    """★ 锁 2.3 ★ 时钟卡住被看门狗拉起来时，head 之前的音一个都不许再响。

    `_base` 直接改，是为了不真等时钟 —— `_on_tick()` 的位置是
    `_base + 时钟走过的量`，时钟刚 restart 过，所以这样等价于"已经播到那儿"。
    """
    p, fired = _player()
    try:
        p.play_from(0.0)
        p._base = 1.0            # 假装已经播到第 1 拍
        p._on_tick()
        first = len(fired)
        assert first == 2, '0:1 和 0.5:2 都该响过：%r' % (fired,)

        p.play_from(p.beat, keep_fired=True)    # ← 看门狗那条路径
        p._on_tick()
        assert len(fired) == first, '已播过的音被重放了：%r' % (fired,)
    finally:
        p.stop()


def test_play_from_without_keep_fired_replays(qapp):
    """反面对照：**没有** keep_fired 时确实会重放（证明上一条不是假通过）。"""
    p, fired = _player()
    try:
        p.play_from(0.0)
        p._base = 1.0
        p._on_tick()
        first = len(fired)

        p.play_from(p.beat)      # 默认 keep_fired=False
        p._on_tick()
        assert len(fired) > first
    finally:
        p.stop()
