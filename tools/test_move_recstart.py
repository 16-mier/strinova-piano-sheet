# -*- coding: utf-8 -*-
"""验证三处修复（用户报的三个问题）。

  ① 「这个显示谱面的窗口无法移动」
       谱面窗整窗鼠标穿透 → 本体抓不住。现在它顶上叠了一个**独立的
       小把手窗口**（不穿透），按住就能拖。
       这里验：把手真的生出来、摆在浮窗顶上、压在浮窗之上（Win32
       WindowFromPoint）、拖它能把浮窗带走、且它自己带 WS_EX_NOACTIVATE
       （拖它不会让游戏失焦）。

  ② 「我把红线移到后面他会从头开始来」
       节拍记录的起点原来写死 `_play_from_beat(0.0)`。
       这里验：红线先拖到 12 秒，再按第一个音 —— 起点必须是 12 秒。

  ③ 顺带验「按住时间轴时播放器不许抢播放头」——
       不然拖红线会被每 12 ms 一次的 tick 拽回去。

★ 用**真实平台**跑（不是 offscreen）★
  把手靠的是 Win32 扩展样式（WS_EX_TRANSPARENT / WS_EX_NOACTIVATE）
  和真实的窗口 Z 序，offscreen 平台下这些全是假的，测不出东西来。
  窗口会闪一下，跑完自动关。

    python tools/test_move_recstart.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

# 没字体的话 offscreen/真实平台都可能画不出字 —— 提前指好
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt          # noqa: E402
from PyQt6.QtGui import QMouseEvent                           # noqa: E402
from PyQt6.QtTest import QTest                                # noqa: E402
from PyQt6.QtWidgets import QApplication                      # noqa: E402

from ui.editor import EditorDialog                            # noqa: E402
from ui.overlay import (WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,    # noqa: E402
                        WS_EX_TRANSPARENT, OverlayWindow)

_user32 = ctypes.windll.user32

BAD = 0


def ok(cond: bool, good: str, bad_msg: str):
    global BAD
    if cond:
        print('  ✅ %s' % good)
    else:
        print('  ❌ %s' % bad_msg)
        BAD += 1
    return cond


def pump(app, ms: int = 60):
    """跑一小会儿事件循环（让 singleShot / QTimer 有机会执行）。"""
    import time
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.005)


def send_move(widget, gx: int, gy: int, buttons=Qt.MouseButton.LeftButton):
    """造一个**带 buttons** 的鼠标移动事件。

    ★ 不能用 `QTest.mouseMove` ★
      它发出来的事件 `buttons()` 是空的，而所有拖动逻辑都靠
      `event.buttons() & LeftButton` 判断"是不是在拖"——
      用它测拖动会永远测出"没动"。
    """
    p = QPointF(gx, gy)
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(5.0, 5.0), p, p,
                     Qt.MouseButton.NoButton, buttons,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# ----------------------------------------------------------------------
# ① 把手
# ----------------------------------------------------------------------

def test_handle(app) -> None:
    print('\n[1] 谱面窗的「把手」')
    ov = OverlayWindow()
    ov.setGeometry(120, 120, 470, 580)
    ov.show()
    pump(app, 200)

    h = ov.handle
    ok(h.isVisible(), '把手跟着浮窗一起露出来了',
       '把手没显示 —— 还是拖不动')

    g = h.geometry()
    ok((g.x(), g.y()) == (ov.x(), ov.y()),
       '把手贴在浮窗左上角 (%d, %d)' % (g.x(), g.y()),
       '把手位置不对：把手 (%d,%d) vs 浮窗 (%d,%d)'
       % (g.x(), g.y(), ov.x(), ov.y()))
    ok(g.width() == ov.width(),
       '把手宽度跟着浮窗走（%d px）' % g.width(),
       '把手宽度 %d ≠ 浮窗宽度 %d' % (g.width(), ov.width()))

    # ---- 扩展样式：把手**不穿透**，且绝不抢焦点 ----
    ex = h.ex_style()
    ok(not (ex & WS_EX_TRANSPARENT),
       '把手不穿透（WS_EX_TRANSPARENT 没设）→ 抓得住',
       '把手竟然带了 WS_EX_TRANSPARENT，鼠标会穿过去')
    ok(bool(ex & WS_EX_NOACTIVATE),
       '把手带 WS_EX_NOACTIVATE → 拖它不会让游戏失焦',
       '把手没有 WS_EX_NOACTIVATE，拖一下游戏就失焦了')
    ok(bool(ex & WS_EX_TOOLWINDOW), '把手不占任务栏', '把手会出现在任务栏里')

    # 对照：浮窗本体**必须**还是穿透的
    ov_ex = ov.ex_style()
    ok(bool(ov_ex & WS_EX_TRANSPARENT),
       '浮窗本体仍然鼠标穿透（枪的准星照样打过去）',
       '浮窗本体不穿透了 —— 会挡住游戏里的点击')

    # ---- Z 序：把手得压在浮窗之上，否则点不到 ----
    _user32.WindowFromPoint.restype = _wt.HWND
    _user32.WindowFromPoint.argtypes = [_wt.POINT]
    pt = _wt.POINT(ov.x() + 60, ov.y() + 8)      # 落在把手正中间
    top = int(_user32.WindowFromPoint(pt) or 0)
    ok(top == int(h.winId()),
       '把手压在浮窗之上（该点命中把手 hwnd=%d）' % top,
       '该点命中的是 hwnd=%d（把手=%d）—— 把手被压住了，点不到'
       % (top, int(h.winId())))

    # ---- 真拖一把 ----
    x0, y0 = ov.x(), ov.y()
    QTest.mousePress(h, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    pump(app, 30)
    ok(h._off is not None, '按在把手上，进入拖动状态', '按下没进拖动状态')
    for dx, dy in ((40, 0), (80, 30), (120, 70)):
        send_move(h, x0 + dx + 5, y0 + dy + 5)
        pump(app, 25)
    QTest.mouseRelease(h, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    pump(app, 60)

    moved = (ov.x() - x0, ov.y() - y0)
    ok(moved[0] > 50 and moved[1] > 30,
       '拖把手 → 浮窗跟着挪了 %s' % (moved,),
       '浮窗没跟着动（位移 %s）' % (moved,))
    hg = h.geometry()
    ok((hg.x(), hg.y()) == (ov.x(), ov.y()),
       '拖完把手仍然贴在浮窗左上角',
       '拖完把手和浮窗分家了：把手 (%d,%d) 浮窗 (%d,%d)'
       % (hg.x(), hg.y(), ov.x(), ov.y()))

    # ---- 隐身 / 关掉时把手要一起收起来 ----
    ov.set_ghost(True)
    pump(app, 40)
    ok(not h.isVisible(), '浮窗隐身时把手也跟着收起',
       '浮窗隐身了把手还挂着 —— 看着像出错了')
    ov.set_ghost(False)
    pump(app, 40)
    ok(h.isVisible(), '浮窗现身时把手回来', '把手没回来')

    ov.set_handle_visible(False)
    pump(app, 40)
    ok(not h.isVisible(), '控制台里取消勾选 → 把手收起', '开关没生效')
    ov.set_handle_visible(True)
    pump(app, 40)

    ov.hide()
    ov.close()
    pump(app, 80)


# ----------------------------------------------------------------------
# ② 节拍记录的起点
# ----------------------------------------------------------------------

def test_rec_start(app) -> None:
    print('\n[2] 节拍记录的起点跟着红线走')
    dlg = EditorDialog()
    dlg.text.setPlainText('0:5\n1:3\n')
    dlg.show()
    pump(app, 150)

    te = dlg.tl_edit

    # ---- 先点「⏺ 节拍记录」----
    # ★ 编辑器一打开时钟就在**静音**自走，所以这里必须能进预备 ★
    #   原来的判据是 `_rec_armed or player.playing`，静音自走也算"在播"，
    #   于是第一次点永远是"停"，得点两下 —— 顺手修掉了。
    dlg._toggle_record_mode()
    pump(app, 60)
    ok(dlg._rec_armed, '点一下就进了预备（不用点两下）',
       '没进预备状态 —— 静音自走的时钟被当成了"正在记录"')
    ok(not dlg.player.playing,
       '预备期间时钟是停着的（免得你手还没到打击垫，时间轴已经跑了）',
       '预备期间时钟还在跑 —— 看门狗把它拉起来了')

    # ---- 预备中把红线拖到 12 秒（这就是用户的操作）----
    want_beat = 12.0 / te.spb
    te.set_playhead(want_beat)
    pump(app, 120)                 # 跑久一点，让看门狗（150ms）有机会插手
    print('  预备中把红线拖到 12.00 秒 → 播放头 %.2f 拍（%.2f 秒）'
          % (te.playhead, te.playhead * te.spb))
    ok(abs(te.playhead - want_beat) < 1e-6,
       '红线老老实实停在 12 秒，没被抢回去',
       '红线被抢回 %.2f 拍了' % te.playhead)

    # ---- 第一个音 —— 计时从这一刻开始，起点应该是红线所在处 ----
    n0 = len(dlg.model.notes)
    dlg._on_pad_click('5')
    print('  按下第一个音：_rec_from = %.3f 拍（= %.2f 秒）'
          % (dlg._rec_from, dlg._rec_from * te.spb))
    ok(abs(dlg._rec_from - want_beat) < 1e-6,
       '起点跟着红线走（第 %.1f 拍）' % dlg._rec_from,
       '起点还是被写死成 %.1f 拍（应该是 %.1f）'
       % (dlg._rec_from, want_beat))

    pump(app, 250)          # 让 QTimer.singleShot(0, _rec_start) 跑掉
    head = te.playhead
    print('  时间轴现在在 %.2f 拍（%.2f 秒），playing=%s'
          % (head, head * te.spb, dlg.player.playing))
    ok(head >= want_beat - 1e-6 and head < want_beat + 3.0,
       '时间轴从红线那儿接着走，没有跳回开头',
       '时间轴跳到 %.2f 拍了 —— 就是用户说的"从头开始来"' % head)
    ok(dlg.player.playing, '时钟跑起来了', '时钟没跑起来')
    ok(dlg.player.muted, '记录模式是静音的（只走时间不出声）',
       '记录模式竟然会出声')

    # ---- 第一个音落在哪 ----
    if len(dlg.model.notes) > n0:
        n = dlg.model.notes[-1]
        at = n.start * te.spb
        print('  第一个音落在 %.2f 秒' % at)
        ok(abs(at - 12.0) < 1.0,
           '第一个音落在 12 秒附近（接在后面录）',
           '第一个音落在 %.2f 秒 —— 不是接着红线那儿' % at)
    else:
        ok(False, '', '按了打击垫却没写进音')

    dlg._stop_record()
    pump(app, 40)
    dlg.player.stop()
    dlg.close()
    pump(app, 60)


# ----------------------------------------------------------------------
# ③ 按住时间轴时播放器不抢播放头
# ----------------------------------------------------------------------

def test_mouse_guard(app) -> None:
    print('\n[3] 按住时间轴时播放器不许抢播放头')
    dlg = EditorDialog()
    dlg.text.setPlainText('0:5\n1:3\n2:5\n3:3\n')
    dlg.show()
    pump(app, 150)
    dlg.player.stop()
    pump(app, 60)

    te = dlg.tl_edit
    te.setFocus()
    pump(app, 40)

    QTest.mousePress(te, Qt.MouseButton.LeftButton, pos=QPoint(400, 40))
    pump(app, 40)
    ok(te.mouse_held, '按下左键 → 进入"用户操作中"',
       '按下后 mouse_held 还是 False（自愈逻辑把它清掉了？）')

    te.set_playhead(90.0)                 # 模拟拖红线到第 90 拍
    dlg._on_player_tick(3.0)              # 播放器想把它设回第 3 拍
    ok(abs(te.playhead - 90.0) < 1e-9,
       '拖动期间 tick 没把播放头抢回去（还是第 %.1f 拍）' % te.playhead,
       '播放头被 tick 抢到第 %.1f 拍了' % te.playhead)

    QTest.mouseRelease(te, Qt.MouseButton.LeftButton, pos=QPoint(400, 40))
    pump(app, 40)
    ok(not te.mouse_held, '松开 → 交还给播放器', '松开后还占着')

    dlg._on_player_tick(3.0)
    ok(abs(te.playhead - 3.0) < 1e-9,
       '松手后 tick 正常更新播放头（第 %.1f 拍）' % te.playhead,
       '松手后播放头不更新了（第 %.1f 拍）—— 会看起来"卡住"' % te.playhead)

    dlg.player.stop()
    dlg.close()
    pump(app, 60)


def test_follow_editor(app) -> None:
    """浮窗的「按键显示跟随制谱器」要真的接上。"""
    print('\n[4] 浮窗按键显示跟随制谱器')
    from ui.control import ControlWindow
    from ui.overlay import Player

    player = Player()
    ov = OverlayWindow()
    w = ControlWindow(player, ov)
    player.tick.connect(ov.set_time)
    ov.show()
    w.show()
    pump(app, 400)

    base_tl = w.tl
    ok(base_tl is not None, '控制台载入了一份谱面', '控制台没载入谱面')

    # 造一个制谱器，接上控制台（和 `_edit_sheet` 里做的事一样）
    dlg = EditorDialog(w.path, w)
    w._editor = dlg
    dlg.timeline_tick.connect(w._on_editor_tick)
    dlg.text.setPlainText('0:1\n1:5\n2:3\n3:5\n')
    dlg.show()
    pump(app, 300)

    follow = w.chk_follow_editor.isChecked()
    ok(follow, '「浮窗按键跟随制谱器」默认是开着的', '这个开关默认关着')

    # ★ 这一份和上面 [2] 用的谱面不一样，正好能验出浮窗换没换时间轴 ★
    dlg.text.setPlainText('0:7\n1:1\n2:2\n3:4\n4:6\n')
    dlg._refresh_from_text()
    pump(app, 200)
    dlg._play_from_beat(0.0)
    pump(app, 250)

    tl_now = ov.view.timeline
    ok(tl_now is not None and tl_now is not base_tl,
       '浮窗换成了制谱器那份谱面（不再画控制台载入的）',
       '浮窗还在画控制台那份谱面 —— 跟随没接上')
    if tl_now is not None:
        got = sorted({p for it in tl_now.items for p in it.chord.pitches})
        print('  浮窗上的音 = %s' % got)
        ok(set(got) == {'7', '1', '2', '4', '6'},
           '浮窗画的就是制谱器里那份（7 1 2 4 6）',
           '浮窗上的音不对：%s' % got)

    sec1 = ov.view.sec
    pump(app, 300)
    sec2 = ov.view.sec
    print('  浮窗时间 %.3f → %.3f 秒' % (sec1, sec2))
    ok(sec2 > sec1 + 0.05,
       '制谱器在走，浮窗的播放头跟着走',
       '浮窗的播放头没动（%.3f → %.3f）' % (sec1, sec2))

    # ---- 关掉制谱器：浮窗回到控制台那份 ----
    dlg.close()
    w._close_editor(dlg)
    pump(app, 200)
    ok(ov.view.timeline is base_tl,
       '关掉制谱器后浮窗回到控制台载入的谱面',
       '关掉制谱器后浮窗没还回来')

    w.close()
    pump(app, 200)


def main() -> int:
    app = QApplication([])
    print('=' * 72)
    print('谱面窗可拖动 / 节拍记录起点 / 播放头不被抢 / 跟随制谱器')
    print('（真实平台，窗口会闪一下）')
    print('=' * 72)

    test_handle(app)
    test_rec_start(app)
    test_mouse_guard(app)
    test_follow_editor(app)

    print('')
    print('=' * 72)
    print('结果：%s' % ('全部通过' if BAD == 0 else '有 %d 项不合格' % BAD))
    print('=' * 72)
    return 0 if BAD == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
