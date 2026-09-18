# -*- coding: utf-8 -*-
"""`ui/edit_player.py` 的时钟测试。

★ 这一组是冲着用户报的「跟随有延迟」来的 ★

  `EditPlayer._on_tick()` 原来用 `spb = 60 / bpm`（谱面 BPM）算拍数，
  而 `TimelineEditor.spb` 是**固定的 0.5**。两个基准不一样，
  于是曲子 BPM ≠ 120 时界面会**越走越落后**：

      曲子 BPM 100 → 播放器认为 1 拍 = 0.6 秒，界面认为 0.5 秒
      真实过了 1 秒，红线/浮窗只前进 0.833 秒 —— 落后 16.7%

  用户看到的就是"浮窗高亮的音总比听到的慢半拍，曲子越长落得越多"。

  修法：统一到 `SPB`。下面第一条测试就是钉死这件事的。
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtTest import QTest                              # noqa: E402
from PyQt6.QtWidgets import QApplication                    # noqa: E402

from core import parser                                     # noqa: E402
from core.edit_model import SPB, EditModel                  # noqa: E402
from ui.edit_player import EditPlayer                       # noqa: E402

SHEET = '0:1\n1:2\n2:3\n3:4\n5:5\n8:6\n'


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _player(bpm: int) -> EditPlayer:
    p = EditPlayer()
    p.set_model(EditModel(parser.parse(SHEET)))
    p.bpm = bpm                 # 故意设成非 120
    return p


# ----------------------------------------------------------------------
# 时钟基准
# ----------------------------------------------------------------------

@pytest.mark.parametrize('bpm', [60, 100, 120, 240])
def test_clock_tracks_wall_clock(qapp, bpm):
    """★ 核心断言：界面上显示的秒数必须等于真实经过的秒数 ★

    `bpm` 取多少都不该影响这件事 —— 谱面位置本来就是绝对秒，
    BPM 只是记谱时的历史信息。
    """
    p = _player(bpm)
    p.play_from(0.0)
    try:
        QTest.qWait(400)
        shown = p.beat * SPB          # 拍 -> 秒，界面就是这么换算的
        assert abs(shown - 0.4) < 0.12, (
            'BPM %d 时界面显示 %.3f 秒，实际过了 0.4 秒（差 %.1f%%）'
            % (bpm, shown, 100.0 * (shown - 0.4) / 0.4))
    finally:
        p.stop()


def test_bpm_does_not_change_speed(qapp):
    """不同 BPM 下走同样长的时间，显示的秒数应当一样。

    这一条把"两个基准打架"这个 bug 本身钉死 ——
    修之前 BPM 100 会比 BPM 120 慢 17%。
    """
    got = {}
    for bpm in (100, 200):
        p = _player(bpm)
        p.play_from(0.0)
        try:
            QTest.qWait(300)
            got[bpm] = p.beat * SPB
        finally:
            p.stop()
    assert abs(got[100] - got[200]) < 0.06, got


def test_play_from_midway_keeps_offset(qapp):
    """从中间起播时，偏移量要保住（不能重置成 0）。"""
    p = _player(100)
    p.play_from(3.0 / SPB)            # 从第 3 秒处
    try:
        QTest.qWait(200)
        shown = p.beat * SPB
        assert 3.0 <= shown < 3.3, shown
    finally:
        p.stop()


def test_stop_then_play_resumes(qapp):
    """停下再起播是从原地接着走，不是回开头。"""
    p = _player(120)
    p.play_from(2.0 / SPB)
    QTest.qWait(150)
    p.stop()
    mid = p.beat
    p.play_from(p.beat)               # 模拟界面上的"接着播"
    try:
        QTest.qWait(150)
        assert p.beat > mid
    finally:
        p.stop()
