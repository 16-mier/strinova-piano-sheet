# -*- coding: utf-8 -*-
"""前台窗口探测 —— 判断「卡拉彼丘现在是不是正在前台」。

用途：谱面浮窗可以设成「只在卡丘窗口在前面时才露面」，
切出去看网页 / 打字的时候它就自动隐身，不挡视线。

判据（任一命中即算在前台）
    ① 前台窗口所属进程名里含 calabiyau / strinova
    ② 前台窗口标题里含「卡拉彼丘」/ Strinova（兜底：覆盖 WeGame 覆盖层等）

⚠ 全是只读查询，不碰别人的窗口，不会让游戏失焦。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR,
                                   ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

# 卡拉彼丘的进程名特征（小写子串匹配）。
#   官方启动器 = Calabiyau-Win64-Shipping
#   国际服/其它渠道 = Strinova
GAME_EXE_HINTS = ('calabiyau', 'strinova')
GAME_TITLE_HINTS = ('卡拉彼丘', 'strinova', 'calabiyau')


def exe_of_hwnd(hwnd: int) -> str:
    """某个窗口所属进程的 exe 名（小写、不含 .exe）；取不到返回 ''。"""
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd),
                                         ctypes.byref(pid))
        if not pid.value:
            return ''
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  pid.value)
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                    ctypes.byref(size)):
                return os.path.basename(buf.value).lower().replace('.exe', '')
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        pass
    return ''


def foreground_exe() -> str:
    """前台窗口所属进程的 exe 名（小写、不含 .exe）；取不到返回 ''。"""
    try:
        fg = _user32.GetForegroundWindow()
        return exe_of_hwnd(int(fg)) if fg else ''
    except Exception:
        return ''


def foreground_title() -> str:
    """前台窗口标题；取不到返回 ''。"""
    try:
        fg = _user32.GetForegroundWindow()
        if not fg:
            return ''
        n = _user32.GetWindowTextLengthW(fg)
        if n <= 0:
            return ''
        buf = ctypes.create_unicode_buffer(n + 2)
        _user32.GetWindowTextW(fg, buf, n + 2)
        return buf.value
    except Exception:
        return ''


def is_game_exe(exe: str) -> bool:
    if not exe:
        return False
    e = exe.lower()
    return any(h in e for h in GAME_EXE_HINTS)


def is_game_title(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(h in t for h in GAME_TITLE_HINTS)


def game_in_foreground() -> bool:
    """卡拉彼丘（或其渲染子窗口）现在是不是在最前面。"""
    if is_game_exe(foreground_exe()):
        return True
    return is_game_title(foreground_title())


def describe_foreground() -> str:
    """调试用：'exe｜标题'。"""
    return '%s｜%s' % (foreground_exe() or '?', foreground_title() or '?')


# ======================================================================
# 找游戏窗口 + 拿它的位置（让浮窗能「绑」在游戏窗口上）
# ======================================================================

def _visible_windows() -> list[int]:
    out: list[int] = []

    def _cb(hwnd, _lp):
        try:
            if _user32.IsWindowVisible(hwnd):
                out.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(_cb), 0)
    except Exception:
        pass
    return out


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口的 (x, y, 宽, 高)，**物理像素**；拿不到返回 None。"""
    r = wintypes.RECT()
    try:
        if _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            if w > 0 and h > 0:
                return (int(r.left), int(r.top), w, h)
    except Exception:
        pass
    return None


def find_game_window() -> int:
    """找卡拉彼丘的主窗口句柄；找不到返回 0。

    游戏有时同时存在两个窗口（渲染窗口 / 覆盖层），这里取
    **可见窗口里面积最大的那个** —— 那就是主画面，绑它就对了。
    """
    best, best_area = 0, 0
    for hwnd in _visible_windows():
        if not is_game_exe(exe_of_hwnd(hwnd)):
            continue
        r = window_rect(hwnd)
        if not r:
            continue
        area = r[2] * r[3]
        if area > best_area:
            best, best_area = hwnd, area
    return best
