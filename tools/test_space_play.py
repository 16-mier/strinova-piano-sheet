# -*- coding: utf-8 -*-
"""验证打谱器里「空格 = 播放 / 停止」真的生效。

要同时验两件事，缺一不可：
  1. 焦点在打击垫 / 时间轴上时，空格能播、再按能停
  2. 焦点在**谱面文本框**里时，空格必须老老实实打出一个空格 ——
     记谱法是靠空格排版的，被快捷键抢走就没法打谱了

用 offscreen 平台跑，不弹窗、不用真的按键：

    python tools/test_space_play.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from PyQt6.QtCore import Qt                       # noqa: E402
from PyQt6.QtTest import QTest                    # noqa: E402
from PyQt6.QtWidgets import QApplication          # noqa: E402

from ui.editor import EditorDialog                # noqa: E402


def main() -> int:
    app = QApplication([])
    dlg = EditorDialog()
    dlg.text.setPlainText('1 2 3 4 5 6 7 1\'' * 3)
    dlg.show()
    app.processEvents()

    bad = 0
    print('=' * 72)
    print('打谱器空格键测试（offscreen，不弹窗）')
    print('=' * 72)

    def state(tag: str):
        print('  %-26s playing=%-5s 播放头=%.2f 拍'
              % (tag, dlg.player.playing, dlg.tl_edit.playhead))

    def has_notes() -> bool:
        return bool(dlg.model and dlg.model.notes)

    state('初始')
    if not has_notes():
        print('  ❌ 谱面没解析出音符，后面的播放测试没意义')
        return 1

    # ---- 1. 焦点在时间轴上：空格应该开始播 ----
    # ★ 先停掉自动走的节拍器 ★
    #   制谱器现在一打开时间轴就在走（"一直走就行，就算没有点击按钮"），
    #   不停掉的话第一次按空格是"暂停"，测出来正好相反。
    dlg.player.stop()
    app.processEvents()
    dlg.tl_edit.setFocus()
    app.processEvents()
    print('\n[1] 焦点 = %s' % type(dlg.focusWidget()).__name__)
    QTest.keyClick(dlg.tl_edit, Qt.Key.Key_Space)
    app.processEvents()
    state('按空格')
    if dlg.player.playing:
        print('  ✅ 开始播放了')
    else:
        print('  ❌ 没开始播 —— 事件没冒泡到打谱器窗口')
        bad += 1

    # ---- 2. 再按一次：应该停下，且播放头留在原地 ----
    head_before = dlg.tl_edit.playhead
    QTest.keyClick(dlg.tl_edit, Qt.Key.Key_Space)
    app.processEvents()
    state('再按一次空格')
    if not dlg.player.playing:
        print('  ✅ 停下来了')
    else:
        print('  ❌ 还在播')
        bad += 1
    if abs(dlg.tl_edit.playhead - head_before) < 0.5:
        print('  ✅ 播放头留在原地（再按空格是从这儿接着播）')
    else:
        print('  ⚠ 播放头从 %.2f 跳到了 %.2f'
              % (head_before, dlg.tl_edit.playhead))

    # ---- 3. 焦点在谱面文本框：空格必须打进去，不能触发播放 ----
    dlg.player.stop()
    dlg.text.setFocus()
    app.processEvents()
    print('\n[2] 焦点 = %s' % type(dlg.focusWidget()).__name__)
    before = dlg.text.toPlainText()
    QTest.keyClick(dlg.text, Qt.Key.Key_Space)
    app.processEvents()
    after = dlg.text.toPlainText()
    if len(after) > len(before):
        print('  ✅ 空格打进去了（%d → %d 字）' % (len(before), len(after)))
    else:
        print('  ❌ 空格被快捷键抢走了（%d → %d 字）'
              % (len(before), len(after)))
        bad += 1
    if dlg.player.playing:
        print('  ❌ 而且顺手把播放触发了')
        bad += 1
    else:
        print('  ✅ 没有误触发播放')

    # ---- 4. 带 Ctrl 的空格不该被拦 ----
    dlg.tl_edit.setFocus()
    app.processEvents()
    QTest.keyClick(dlg.tl_edit, Qt.Key.Key_Space,
                   Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    if dlg.player.playing:
        print('\n[3] ❌ Ctrl+空格 被当成播放键了')
        bad += 1
    else:
        print('\n[3] ✅ Ctrl+空格 没被当成播放键')

    print('')
    print('=' * 72)
    print('结果：%s' % ('全部通过' if bad == 0 else '有 %d 项不合格' % bad))
    dlg.player.stop()
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
