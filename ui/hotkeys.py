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

from PyQt6.QtCore import QObject, pyqtSignal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

# 三个用途各自的热键 id（互不冲突，也不跟别处撞）
HK_PLAYPAUSE = 0x6A01
HK_RESTART = 0x6A02
HK_LISTEN = 0x6A03

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


class HotkeyManager(QObject):
    """把全局热键绑到某个窗口上。"""

    fired = pyqtSignal(int)          # 触发时发出热键 id

    def __init__(self, widget, parent=None):
        super().__init__(parent)
        self._widget = widget
        self._bound: dict[int, tuple[int, int]] = {}    # id -> (mods, vk)
        self._errors: dict[int, str] = {}

    # ---------------- 注册 ----------------

    def bind(self, hk_id: int, choice_index: int) -> bool:
        """按 HOTKEY_CHOICES 的下标绑定；下标 0 = 解绑。"""
        self.unbind(hk_id)
        if not (0 <= choice_index < len(HOTKEY_CHOICES)):
            return False
        _label, mods, vk = HOTKEY_CHOICES[choice_index]
        if vk == 0:
            return True                      # 「不绑定」也算成功
        return self._register(hk_id, mods, vk)

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
