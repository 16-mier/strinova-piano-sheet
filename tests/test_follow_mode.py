# -*- coding: utf-8 -*-
"""「📜 跟谱面」和「制谱器打击垫」的契约测试。

★ 这一份原来叫「🎵 跟手」的契约测试 ★
  「跟手」= 听着游戏的声音、你弹一下浮窗走一格。它靠的是
  **起音检测 + 音高闸门**，而被实测反复打脸 —— 用户的环境里
  **永远有音乐在响**（酷狗 + 游戏 BGM）：

      安静段包络中位 0.08316     ← "什么都不播"的 3 秒
      播放段包络中位 0.08049

  两段一样响，所以能量判据在原理上就分不开"我弹的琴"和"正在放的歌"。
  用户最后决定把整个"听音"体系（跟手 / 听音记谱 / 导入音频）全删掉。

★ 留下来的两条路 ★

  1. **📜 跟谱面** —— 浮窗按**谱面里的时间**自己往前走。
     谱面里每个音都有准确时间，所以这条**零延迟、不可能错**，
     而且压根不听声音。
  2. **制谱器打击垫** —— 在制谱器里点一下键，浮窗往前走一格。
     驱动它的是一次**点击**，同样跟"听音"没关系。

  两条都复用 `core/follower.py::Follower` —— 那是个纯计数的位置
  状态机，跟音频没有半点关系（这也是它能留下来的原因）。
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

from core.follower import Follower                          # noqa: E402
from ui.control import ControlWindow                        # noqa: E402
from ui.overlay import OverlayWindow, Player                # noqa: E402


def _mk_tl(pitches):
    """造一个**受控**的小谱面 —— 测试不该依赖 demo.txt 里恰好写了什么。"""
    from core.parser import Chord, Sheet
    from core.timeline import Timeline
    sh = Sheet()
    for i, p in enumerate(pitches):
        sh.events.append(Chord(pitches=[p], duration=0.5, is_rest=False,
                               raw=p, at=float(i) * 0.5))
    return Timeline(sh, default_bpm=120)


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp):
    player = Player()
    overlay = OverlayWindow()
    w = ControlWindow(player, overlay)
    player.tick.connect(overlay.set_time)
    QTest.qWait(200)
    from core.paths import sheets_dir
    demo = os.path.join(sheets_dir(), 'demo.txt')
    if os.path.isfile(demo):
        w.load_sheet(demo)
    yield w
    try:
        w._follow = None
        w.close()
    except Exception:
        pass
    QTest.qWait(60)


# ----------------------------------------------------------------------
# 核心契约：不造识别器
# ----------------------------------------------------------------------




# ----------------------------------------------------------------------
# 前进逻辑
# ----------------------------------------------------------------------






# ----------------------------------------------------------------------
# 音高闸门
# ----------------------------------------------------------------------






# ----------------------------------------------------------------------
# 手动纠正 —— 位置是纯计数的，没有回退就没法用
# ----------------------------------------------------------------------






# ----------------------------------------------------------------------
# 两种"跟"互斥
# ----------------------------------------------------------------------


def test_sheet_follow_works_and_advances(win):
    """「📜 跟谱面」是真的在推浮窗（不是只改了个按钮文字）。"""
    win.btn_live.click()
    QTest.qWait(150)
    t0 = win.overlay.view.sec
    QTest.qWait(400)
    t1 = win.overlay.view.sec
    assert t1 > t0 + 0.2, '跟谱面没在推浮窗：%.3f -> %.3f' % (t0, t1)
    assert win.overlay.view.mark_current is True


# ----------------------------------------------------------------------
# ★ 打击垫 —— 用户：「你直接让这个的逻辑跟着打击垫走就行」★
#
#   为什么这条比"听声音"强得多：打击垫的点击是**同步事件**，
#   你点了哪个键程序当场就知道 —— 不用听、不用认、不会被 BGM 骗。
#   所以这条路上零延迟、零误报、零猜测。
#
#   它补的是「浮窗跟随制谱器」漏掉的那一半：`_on_editor_tick` 跟的是
#   **播放头**，而点打击垫**不改播放头** —— 以前连点 5 下浮窗一直在原地。
# ----------------------------------------------------------------------

def _pad_setup(win, pitches=('1', '2', '3', '5')):
    win.tl = _mk_tl(list(pitches))
    win._editor_tl = None
    win._follow = None
    win._follow_on = False
    return win.tl


def test_pad_hit_advances_the_view(win):
    """点一下打击垫 → 浮窗往前走一格。"""
    tl = _pad_setup(win)
    win._on_pad_hit(['1'])
    f = win._follow
    assert f is not None, '点了一下却没建推进器'
    assert f.index == 1, '点了没往前走：还在第 %d 个' % f.index
    assert win.overlay.view.sec == tl.items[1].start_sec


def test_pad_hit_flashes_the_key_you_actually_pressed(win):
    """★ 闪的是**你实际点的那个键** —— 不是猜的、不是谱面里的 ★

    这条路上不需要音高闸门、不需要置信度：那些东西存在的唯一理由
    就是"从声音里猜"。打击垫直接把键名告诉你。
    """
    _pad_setup(win)
    win.overlay.clear_flash()
    win._on_pad_hit(['5'])
    flashed = set(win.overlay.grid_view.flash)
    assert flashed, '点了却没闪任何键'
    assert '5' in flashed, '闪的不是我刚点的键：%r' % sorted(flashed)


def test_pad_hit_is_not_debounced(win):
    """★ 连点两下必须走两格 ★

    40 ms 那条兜底去抖是给"听声音猜"准备的（一次敲击的能量可能被
    上报两三次）。打击垫点击是同步事件 —— 一个点击就是一个点击。
    """
    _pad_setup(win)
    win._on_pad_hit(['1'])
    win._on_pad_hit(['2'])          # 紧接着，中间没有等待
    assert win._follow.index == 2, '连点被去抖挡掉了'
    assert win._follow.blocked == 0


def test_pad_hit_walks_through_the_whole_sheet(win):
    """连点一整首 —— 不许越界。"""
    tl = _pad_setup(win, ('1', '2', '3'))
    n = len(tl.items)
    for _i in range(n + 3):
        win._on_pad_hit(['1'])
    assert win._follow.index <= n, '越界了：%d / %d' % (win._follow.index, n)


def test_pad_hit_survives_the_sheet_growing(win):
    """★ 制谱器是**边点边长**的 ★

    每点一下谱面就多一个音。位置必须留着 ——
    要是每次都因为"总数变了"而重建推进器，位置会一路退回开头，
    表现就是"越点越退"。
    """
    _pad_setup(win, ('1', '2'))
    win._on_pad_hit(['1'])
    assert win._follow.index == 1
    # 谱面长长了一格（模拟制谱器又写进去一个音）
    win.tl = _mk_tl(['1', '2', '3'])
    win._on_pad_hit(['3'])
    assert win._follow.index == 2, '谱面长长之后位置被重置了'


def test_pad_hit_resets_when_sheet_shrinks(win):
    """换成一份更短的谱面时要从头跟，不能卡在越界的位置上。"""
    _pad_setup(win, ('1', '2', '3', '5', '6'))
    for _i in range(4):
        win._on_pad_hit(['1'])
    assert win._follow.index == 4
    win.tl = _mk_tl(['1', '2'])              # 换成短的
    win._on_pad_hit(['1'])
    assert win._follow.index <= len(win.tl.items), '位置越界了'


def test_pad_hit_without_sheet_is_harmless(win):
    win.tl = None
    win._editor_tl = None
    win._follow = None
    win._on_pad_hit(['1'])                   # 不该崩
    assert win._follow is None


# ----------------------------------------------------------------------
# ★ 单位回归 —— 「都下俩个按键了显示还是上俩个」的根因 ★
#
#   制谱器那条路交出去的浮窗时间轴整体被**放大了一倍**：
#   `timeline_from_notes()` 的契约是"`.start` 是**秒**"，
#   而 `EdNote.start` 的单位是**拍**（`core/edit_model.py`：`秒 / SPB`），
#   制谱器以前把拍值直接递了进去。
#
#   实测：demo.txt 主界面 28.8 秒 vs 制谱器 55.2 秒。
#   而喂给浮窗的播放位置是真实秒 ⇒ 浮窗只走到"已播放时长的一半"，
#   弹得越久落后越多。真实 2.1~2.7 秒时该弹第 4~5 个音，
#   浮窗深色格停在第 2~3 个 —— **恰好落后两个**。
#
#   ★ 为什么以前 214 条测试全是绿的 ★
#     `_pad_setup()` 显式把 `_editor_tl` 设成 None、也从不打开制谱器，
#     所以所有用例都落在**主界面那条单位正确**的路上 ——
#     `dlg.overlay_timeline()` 一行都没被碰过。
#     下面第二个用例专门补这个缺口。
# ----------------------------------------------------------------------

def test_secnote_converts_beats_back_to_seconds():
    """`_SecNote` 必须把「拍」还原成「秒」。"""
    from core.edit_model import SPB, EditModel
    from core.parser import Chord, Sheet
    from ui.editor import _SecNote

    seconds = [0.0, 0.6, 1.2, 1.8, 2.4]
    sh = Sheet()
    for at in seconds:
        sh.events.append(Chord(pitches=['1'], duration=0.5, is_rest=False,
                               raw='1', at=float(at)))
    m = EditModel(sh)

    # 先确认前提还在：制谱器内部存的确实是「拍」
    assert abs(m.notes[1].start - seconds[1] / SPB) < 1e-9, \
        'EdNote.start 不再是「拍」了 —— 这个测试的前提变了，去看 edit_model'

    # 交出去之前必须换回「秒」
    got = [_SecNote(n, SPB).start for n in m.notes]
    assert got == pytest.approx(seconds), \
        '换算回来对不上：%r vs %r' % (got, seconds)


def test_editor_overlay_timeline_matches_the_main_one(qapp):
    """★ 端到端：制谱器交出的时间轴，和主界面那份**逐项相等** ★

    这条直接钉住用户那句话：「都下俩个按键了显示还是上俩个」。
    """
    from core import parser
    from core.paths import sheets_dir
    from core.timeline import Timeline
    from ui.editor import EditorDialog

    path = os.path.join(sheets_dir(), 'demo.txt')
    if not os.path.isfile(path):
        pytest.skip('没有 demo.txt，跳过')
    main = Timeline(parser.load(path))

    dlg = EditorDialog(path)
    try:
        ed = dlg.overlay_timeline()
        assert ed is not None, '制谱器没交出时间轴'
        assert len(ed.items) == len(main.items), \
            '音数都不一样：%d vs %d' % (len(ed.items), len(main.items))
        bad = []
        for i, (a, b) in enumerate(zip(ed.items, main.items)):
            if abs(a.start_sec - b.start_sec) > 1e-6:
                bad.append('第 %d 个：制谱器 %.3f 秒　主界面 %.3f 秒'
                           % (i + 1, a.start_sec, b.start_sec))
        ratio = ed.total_sec / max(1e-9, main.total_sec)
        assert not bad, (
            '★ 制谱器那条路的时间轴单位和主界面不一致（差 %.2f 倍）★\n  %s'
            % (ratio, '\n  '.join(bad[:5])))
        # ★ 末尾这一条**不能**要求两边 `total_sec` 相等 ★
        #   制谱器那条路的时值一律记 0（`timeline_from_notes` 的 docstring：
        #   "浮窗只看**起点**，不看它响多久"），所以它的 `total_sec` 天然
        #   等于**最后一个音的 start_sec**；而主界面那条会把末音的时值算进去。
        #   浮窗只关心"什么时候该打"，这个差别不影响它。
        #   （判断单位对不对，看的是上面那些 `start_sec`，不是总时长。）
        last_start = main.items[-1].start_sec
        assert abs(ed.total_sec - last_start) < 1e-6, \
            '制谱器的末尾该正好落在最后一个音的起点：%.2f vs %.2f' \
            % (ed.total_sec, last_start)
    finally:
        dlg.close()
        QTest.qWait(40)
