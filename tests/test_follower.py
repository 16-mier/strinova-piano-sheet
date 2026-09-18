# -*- coding: utf-8 -*-
"""`core/follower.py` 的测试 —— 「敲一下走一格」到底走对没有。

★ 为什么这些用例值得写 ★
    在把这些规则抽出来之前，"敲一下往前走一格"是写在
    `ui/control.py::_on_onset()` 里的一段 Qt 信号槽。
    想验证它只有一条路：开程序、进游戏、弹几下、用眼睛看浮窗跳到哪儿了 ——
    而它偏偏是**错了也看不出来**的那种：多走一格，浮窗从此一直超前，
    少走一格就一直落后，两种都表现为"显示的和手上对不上"，
    却分不出是哪一种、也不知道从哪一下开始错的。

    现在它是纯逻辑了，所以能断言"敲 3 下必须**恰好**在第 3 格"。
    这些用例对应的都是真实踩过的形状，不是凑数的。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.follower import MIN_STEP, Follower      # noqa: E402


# ---------------- 基本推进 ----------------

def test_starts_at_first_note():
    f = Follower(10)
    assert f.index == 0
    assert f.pos == 1
    assert not f.at_end


def test_three_kicks_land_exactly_on_third():
    """★ 核心用例 ★ 敲 3 下 = 第 3 个音，不多不少。"""
    f = Follower(10)
    for t in (1.0, 2.0, 3.0):
        assert f.kick(t) == 1
    assert f.index == 3
    assert f.pos == 4          # 弹完第 3 个，接下来该弹第 4 个
    assert f.kicks == 3
    assert f.pushed == 3
    assert f.blocked == 0


def test_kicking_through_the_whole_sheet_reaches_end():
    """整首 5 个音弹 5 下 —— 第 5 下之后应该到结尾。"""
    f = Follower(5)
    for i in range(5):
        f.kick(1.0 + i)
    assert f.at_end
    assert f.pos == 5
    assert f.describe().endswith('（弹完了）')


def test_kick_past_the_end_does_nothing():
    """弹完了再敲不该越界（`index` 不能涨到 count 以上）。"""
    f = Follower(3)
    for i in range(3):
        f.kick(1.0 + i)
    assert f.at_end
    for i in range(5):
        assert f.kick(10.0 + i) == 0
    assert f.index == 3
    assert f.pushed == 3       # 多敲的没算进"推进"


def test_empty_sheet_is_safe():
    f = Follower(0)
    assert f.kick(1.0) == 0
    assert f.at_end
    assert f.pos == 0
    assert f.describe() == '没有谱面'


# ---------------- 去抖兜底 ----------------

def test_too_dense_kicks_are_blocked():
    """40 ms 以内的重复上报要被兜底挡掉，而且**不刷新基准**。

    ★ 不刷新基准这一点很关键 ★
      如果被挡掉时也更新 `_last_t`，那么一串密集上报会
      一个接一个地把基准往后推，永远卡在 40 ms 以内 ——
      于是**整串全被挡**，看起来就像"跟手失灵了"。
      正确行为是：挡住第 2 下，但基准仍停在**最后那次真正推进**上，
      所以 40 ms 一过，下一发照样能推动。
    """
    f = Follower(10)
    assert f.kick(1.000) == 1
    assert f.kick(1.010) == 0      # 10 ms 后 —— 挡
    assert f.kick(1.020) == 0      # 又 10 ms —— 还是挡
    assert f.blocked == 2
    assert f.pushed == 1
    # 距**最后一次有效推进**过了 40 ms —— 应该放行
    assert f.kick(1.045) == 1
    assert f.index == 2


def test_just_over_the_threshold_passes():
    f = Follower(10)
    f.kick(1.0)
    assert f.kick(1.0 + MIN_STEP + 1e-6) == 1


def test_gaps_are_recorded_and_capped():
    """间隔要记下来（控制台自检行靠它），但别无限涨。"""
    f = Follower(100)
    for i in range(40):
        f.kick(1.0 + i * 0.2)
    assert len(f.gaps) <= 10
    assert abs(f.gaps[-1] - 0.2) < 1e-9


# ---------------- 打击垫/真实点击：不去抖 ----------------

def test_press_is_not_debounced():
    """★ 打击垫点击是**同步事件**，不该走 `min_step` 去抖 ★

    `min_step`（40 ms）是给"**听声音猜**"准备的兜底：一次敲击的能量
    会抖动，可能被上报两三次，所以要用时间隔开 —— 那是一条猜测的路。

    而打击垫的点击，一个就是一个。用户连点 5 下（哪怕挤在同一瞬间）
    就该走 5 格。
    """
    f = Follower(10)
    for _i in range(5):
        f.press()
    assert f.index == 5, '连点被挡了：只走了 %d 格' % f.index
    assert f.blocked == 0, '打击垫那条路不该有"挡掉"这回事'
    assert f.kicks == 5


def test_press_and_kick_use_different_rules():
    """两条路的规则不一样，这里把差别钉死。

    混在一起就会两头不讨好：给真实点击加上了没必要的延迟，
    或者让猜测的那条失去兜底。
    """
    # 听声音猜：密了要挡
    k = Follower(10)
    k.kick(1.0)
    k.kick(1.001)
    assert k.index == 1
    assert k.blocked == 1

    # 真实点击：密了也照走
    p = Follower(10)
    p.press()
    p.press()
    assert p.index == 2
    assert p.blocked == 0


def test_press_past_the_end_is_harmless():
    f = Follower(3)
    for _i in range(6):
        f.press()
    assert f.index == 3
    assert f.at_end


# ---------------- 手一动 —— 位置是纯计数的，没有回退就没法用 ----------------

def test_back_undoes_a_kick():
    """★ back() 是唯一能把累积偏差拉回来的东西 ★"""
    f = Follower(10)
    for t in (1.0, 2.0, 3.0):
        f.kick(t)
    assert f.pos == 4
    assert f.back() == 1
    assert f.pos == 3
    assert f.manual == 1
    assert f.pushed == 3       # 手动退格不算"推进"里


def test_back_at_start_does_nothing():
    f = Follower(10)
    assert f.back() == 0
    assert f.index == 0


def test_back_from_end_leaves_the_end():
    """弹完之后还能退回来 —— 不然最后一下判错就没救了。"""
    f = Follower(3)
    for i in range(3):
        f.kick(1.0 + i)
    assert f.at_end
    f.back()
    assert not f.at_end
    assert f.pos == 3


def test_reset_and_goto():
    f = Follower(10)
    for t in (1.0, 2.0, 3.0, 4.0):
        f.kick(t)
    assert f.reset() == 0
    assert f.index == 0
    assert f.goto(7) == 7
    assert f.goto(999) == 10       # 夹到 count，代表"弹完了"
    assert f.goto(-5) == 0


# ---------------- 诊断行 ----------------

def test_diag_reports_counts():
    """自检行是拿来给用户念的 —— 「按 5 下跳了 9 格」必须一眼看出来。"""
    f = Follower(50)
    for i in range(5):
        f.kick(1.0 + i)
    s = f.diag()
    assert '起音 5 次' in s
    assert '推进 5 格' in s
    assert 'ms' in s            # 间隔要显示出来


def test_diag_shows_extra_kicks():
    """★ 真实的坏形状：一次敲击被检测器报了两下 ★
    起音次数 > 推进格数以外的期望值，用户念这一行就能定位问题。
    """
    f = Follower(50)
    t = 1.0
    for _i in range(5):
        f.kick(t)               # 真按
        f.kick(t + 0.15)        # 衰减期被多报一次（间隔够大，挡不住）
        t += 1.0
    assert f.kicks == 10
    assert f.pushed == 10       # 推进器挡不住这种 —— 只能靠检测器
    assert '起音 10 次' in f.diag()


def test_diag_without_gaps():
    f = Follower(10)
    assert '起音 0 次' in f.diag()


# ---------------- 和真实谱面对象对得上 ----------------

def test_count_from_a_real_timeline():
    """`Follower(len(tl.items))` 是最常见的构造方式 —— 对一下长度语义。"""
    from core.parser import Chord, Sheet
    from core.timeline import Timeline

    sh = Sheet()
    for i, p in enumerate(['1', '2', '3', '5', '5']):
        sh.events.append(Chord(pitches=[p], duration=0.5, is_rest=False,
                               raw=p, at=float(i) * 0.5))
    tl = Timeline(sh, default_bpm=120)
    f = Follower(len(tl.items))
    assert f.total == 5
    for i in range(5):
        f.kick(1.0 + i)
    assert f.at_end
    assert f.pos == 5
