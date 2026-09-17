# -*- coding: utf-8 -*-
"""全局热键 —— 在游戏里也能控制谱面，而且**不抢游戏焦点**。

★ 为什么不抢焦点 ★
    RegisterHotKey 是系统级注册：按下时系统把 WM_HOTKEY 直接投递到指定窗口的
    消息队列，**不激活窗口、也不改变前台窗口** —— 游戏该在前台还在前台，
    鼠标锁定也不会被解除（这正是之前「鼠标飘出游戏窗口」的成因）。

★ 代价 ★
    注册之后这个键会被系统截走，游戏收不到它。
    所以别绑游戏要用的键（WASD、空格、Q/E、V/U 之类），
    用 F 系列或小键盘最稳。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

# 四个用途各自的热键 id（互不冲突，也不跟别处撞）
HK_PLAYPAUSE = 0x6A01
HK_RESTART = 0x6A02
HK_LISTEN = 0x6A03
HK_TOGGLE = 0x6A04          # 显示 / 隐藏谱面浮窗

_user32 = ctypes.windll.user32
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                   wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL


def _vk_f(n: int) -> int:
    return 0x70 + (n - 1)          # F1 = 0x70


HOTKEY_CHOICES: list[tuple[str, int, int]] = [
    ('不绑定', 0, 0),
    # 无修饰的 F 键 —— 最安全，游戏基本不用
    *[('F%d' % i, 0, _vk_f(i)) for i in range(1, 13)],
    # 小键盘
    *[('小键盘 %d' % i, 0, 0x60 + i) for i in range(0, 10)],
    # 带修饰的组合（更不容易误触）
    *[('Ctrl+F%d' % i, MOD_CONTROL, _vk_f(i)) for i in range(1, 13)],
    *[('Alt+F%d' % i, MOD_ALT, _vk_f(i)) for i in range(1, 13)],
    *[('Shift+F%d' % i, MOD_SHIFT, _vk_f(i)) for i in range(1, 13)],
    # 数字行（带修饰才安全）
    *[('Ctrl+%d' % i, MOD_CONTROL, 0x30 + i) for i in range(1, 10)],
    *[('Alt+%d' % i, MOD_ALT, 0x30 + i) for i in range(1, 10)],
]

HOTKEY_LABELS = [c[0] for c in HOTKEY_CHOICES]


def choice_index_by_name(name: str) -> int:
    for i, (label, _m, _v) in enumerate(HOTKEY_CHOICES):
        if label == name:
            return i
    return 0


# ======================================================================
# 键名 / 绑定的读写
# ======================================================================

_VK_SPECIAL = {
    0x08: '退格', 0x09: 'Tab', 0x0D: '回车', 0x13: 'Pause', 0x14: 'CapsLock',
    0x1B: 'Esc', 0x20: '空格', 0x21: 'PgUp', 0x22: 'PgDn', 0x23: 'End',
    0x24: 'Home', 0x25: '←', 0x26: '↑', 0x27: '→', 0x28: '↓',
    0x2D: 'Insert', 0x2E: 'Delete',
    0xBA: ';', 0xBB: '=', 0xBC: ',', 0xBD: '-', 0xBE: '.', 0xBF: '/',
    0xC0: '`', 0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'",
}

# 游戏里自己要用的键 —— 绑上去游戏就收不到它了，界面上给红字提醒
RISKY_VK = (
    {ord(c) for c in 'WASDQRETFGHVU'}                # 移动 / 技能 / 交互
    | set(range(0x30, 0x3A))                         # 数字行（切枪、买东西）
    | {0x20, 0x09, 0x0D, 0x1B}                       # 空格 / Tab / 回车 / Esc
)


def vk_name(vk: int) -> str:
    """虚拟键码 -> 人类可读的键名。"""
    if vk in _VK_SPECIAL:
        return _VK_SPECIAL[vk]
    if 0x70 <= vk <= 0x87:
        return 'F%d' % (vk - 0x70 + 1)
    if 0x60 <= vk <= 0x69:
        return '小键盘%d' % (vk - 0x60)
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)                               # Qt/VK 在 ASCII 段一致
    return 'VK%02X' % vk


def binding_label(mods: int, vk: int) -> str:
    if not vk:
        return '不绑定'
    pre = ''
    if mods & MOD_CONTROL:
        pre += 'Ctrl+'
    if mods & MOD_ALT:
        pre += 'Alt+'
    if mods & MOD_SHIFT:
        pre += 'Shift+'
    if mods & MOD_WIN:
        pre += 'Win+'
    return pre + vk_name(vk)


def parse_binding(value, default=(0, 0)) -> tuple[int, int]:
    """配置里存的值 -> (mods, vk)。

    兼容两种格式：新的 `[mods, vk]`，以及旧版的键名字符串（'F8' 这种）。
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return default
    if isinstance(value, str) and value:
        for label, m, v in HOTKEY_CHOICES:
            if label == value:
                return m, v
    return default


_QT_EXTRA_VK = {
    Qt.Key.Key_Space: 0x20, Qt.Key.Key_Escape: 0x1B, Qt.Key.Key_Tab: 0x09,
    Qt.Key.Key_Return: 0x0D, Qt.Key.Key_Enter: 0x0D,
    Qt.Key.Key_Backspace: 0x08, Qt.Key.Key_Delete: 0x2E,
    Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23,
    Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22,
    Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26,
    Qt.Key.Key_Right: 0x27, Qt.Key.Key_Down: 0x28,
    Qt.Key.Key_Minus: 0xBD, Qt.Key.Key_Equal: 0xBB,
    Qt.Key.Key_BracketLeft: 0xDB, Qt.Key.Key_BracketRight: 0xDD,
    Qt.Key.Key_Backslash: 0xDC, Qt.Key.Key_Semicolon: 0xBA,
    Qt.Key.Key_Apostrophe: 0xDE, Qt.Key.Key_Comma: 0xBC,
    Qt.Key.Key_Period: 0xBE, Qt.Key.Key_Slash: 0xBF,
    Qt.Key.Key_QuoteLeft: 0xC0,
}


def qt_key_to_vk(key) -> int:
    """Qt 键值 -> Windows VK（nativeVirtualKey 拿不到时的兜底）。"""
    k = int(key)
    if 0x41 <= k <= 0x5A or 0x30 <= k <= 0x39:      # A-Z / 0-9 段完全一致
        return k
    if 0x70 <= k <= 0x87:                            # F1-F24
        return k
    return _QT_EXTRA_VK.get(key, 0)


class HotkeyEdit(QPushButton):
    """点一下 → 直接按下想绑的键 → 就绑好了。

    比下拉列表直观得多：习惯用哪个键就按哪个。
    录制中：Esc = 取消，Delete / Backspace = 清除绑定。
    """

    changed = pyqtSignal()
    recording_changed = pyqtSignal(bool)     # 录制中要临时禁掉别处的快捷键

    def __init__(self, mods: int = 0, vk: int = 0, parent=None):
        super().__init__(parent)
        self.mods = int(mods)
        self.vk = int(vk)
        self._recording = False
        self.setMinimumWidth(160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start)
        self._refresh()

    # ---- 显示 ----

    def _refresh(self):
        if self._recording:
            self.setText('按下想要的键…')
            self.setStyleSheet(
                'QPushButton{color:#ffd230;font-weight:bold;'
                'border:2px solid #ffd230;}')
            return
        self.setText(binding_label(self.mods, self.vk))
        if self.vk and self.vk in RISKY_VK:
            self.setStyleSheet('QPushButton{color:#ff9a9a;}')
            self.setToolTip('⚠ 这个键游戏里要用（移动 / 技能 / 切枪…）。\n'
                            '绑上之后游戏就收不到它了，建议用 F 系列或小键盘。')
        else:
            self.setStyleSheet('')
            self.setToolTip('点一下，然后按下想用的键。\n'
                            '录制中：Esc = 取消，Delete = 清除绑定')

    def set_binding(self, mods: int, vk: int):
        self.mods, self.vk = int(mods), int(vk)
        self._refresh()

    def clear_binding(self):
        self.set_binding(0, 0)

    # ---- 录制 ----

    def _start(self):
        self._recording = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._refresh()
        self.recording_changed.emit(True)

    def _stop(self):
        if not self._recording:
            return
        self._recording = False
        self._refresh()
        self.recording_changed.emit(False)

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            return
        key = event.key()

        if key == Qt.Key.Key_Escape:                 # 取消
            self._stop()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):   # 清除
            self.set_binding(0, 0)
            self._stop()
            self.changed.emit()
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_unknown):
            return                                   # 只按了修饰键，继续等

        mods = 0
        m = event.modifiers()
        if m & Qt.KeyboardModifier.ControlModifier:
            mods |= MOD_CONTROL
        if m & Qt.KeyboardModifier.AltModifier:
            mods |= MOD_ALT
        if m & Qt.KeyboardModifier.ShiftModifier:
            mods |= MOD_SHIFT
        if m & Qt.KeyboardModifier.MetaModifier:
            mods |= MOD_WIN

        vk = int(event.nativeVirtualKey() or 0) or qt_key_to_vk(key)
        if not vk:
            return
        self._stop()
        self.set_binding(mods, vk)
        self.changed.emit()

    def focusOutEvent(self, event):
        self._stop()
        super().focusOutEvent(event)


class HotkeyManager(QObject):
    """把全局热键绑到某个窗口上。"""

    fired = pyqtSignal(int)          # 触发时发出热键 id

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self._widget = widget
        self._bound: dict[int, tuple[int, int]] = {}    # id -> (mods, vk)
        self._errors: dict[int, str] = {}

    # ---------------- 注册 ----------------

    def bind(self, hk_id: int, mods: int, vk: int) -> bool:
        """绑一个 (修饰键, 虚拟键码)。vk = 0 表示「不绑定」。"""
        self.unbind(hk_id)
        if not vk:
            self._errors.pop(hk_id, None)
            return True                      # 「不绑定」也算成功
        return self._register(hk_id, int(mods), int(vk))

    def _register(self, hk_id: int, mods: int, vk: int) -> bool:
        try:
            hwnd = int(self._widget.winId())
        except Exception as e:
            self._errors[hk_id] = '拿不到窗口句柄: %r' % e
            return False
        try:
            ok = _user32.RegisterHotKey(ctypes.c_void_p(hwnd), hk_id,
                                        mods | MOD_NOREPEAT, vk)
            if ok:
                self._bound[hk_id] = (mods, vk)
                self._errors.pop(hk_id, None)
                return True
            err = ctypes.get_last_error() if hasattr(
                ctypes, 'get_last_error') else 0
            self._errors[hk_id] = (
                '注册失败（这个组合可能已被别的程序占用）err=%s' % err)
            return False
        except Exception as e:
            self._errors[hk_id] = '注册异常: %r' % e
            return False

    def unbind(self, hk_id: int):
        if hk_id not in self._bound:
            return
        try:
            hwnd = int(self._widget.winId())
            _user32.UnregisterHotKey(ctypes.c_void_p(hwnd), hk_id)
        except Exception:
            pass
        self._bound.pop(hk_id, None)

    def unbind_all(self):
        for hk_id in list(self._bound):
            self.unbind(hk_id)

    # ---------------- 查询 ----------------

    def is_bound(self, hk_id: int) -> bool:
        return hk_id in self._bound

    def last_error(self, hk_id: int) -> str:
        return self._errors.get(hk_id, '')

    # ---------------- 消息 ----------------

    def handle_native(self, msg: int, wparam: int) -> bool:
        """在窗口的 nativeEvent 里调用。命中返回 True（表示已处理）。"""
        if msg != WM_HOTKEY:
            return False
        hk_id = int(wparam)
        if hk_id in self._bound:
            self.fired.emit(hk_id)
        return True
