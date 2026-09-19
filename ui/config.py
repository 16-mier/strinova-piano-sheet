# -*- coding: utf-8 -*-
"""配置的读写 —— **纯函数，不碰任何 Qt 控件**。

★ 为什么把它单独抽出来 ★

  原来 `ControlWindow._load_config` / `_save_config` 加起来 99 行，
  和 UI 状态初始化纠缠在一起，有三个具体毛病：

  1. **字段名散在两处**：读的时候写一遍 `cfg.get('bg_alpha', 100)`，
     存的时候再写一遍 `'bg_alpha': self.sld_bg.value()`。
     改个字段名很容易漏改另一半，而漏了**不会报错**，
     只会"设置改了重启就丢"。默认值现在集中在 `DEFAULTS` 里。

  2. **启动期反复写盘**：`_load_config` 里 `sp_x/sp_y/sp_w/sp_h.setValue()`
     会触发 `valueChanged` → `_apply_geometry` → `_save_config` ——
     设四个值就写四次盘，而那时其它控件还没初始化完，
     写进去的是**半成品配置**。（`control.py` 那边现在把信号掐掉了。）

  3. **没法单测**：读配置这件事要造一个 `QMainWindow` 才能验。
     现在给一份 dict 就能测（见 `tests/test_config.py`）。

★ 这一层只负责三件事 ★
    · 文件 ⇄ dict
    · 缺字段补默认值、类型不对的纠正回来（配置文件是用户能直接编辑的）
    · 老字段/老取值的迁移
  至于"dict 的每个键对应哪个控件"，那是 `control.py` 的事。
"""

from __future__ import annotations

import json
import os
import tempfile

from core.paths import config_path


# ----------------------------------------------------------------------
# 默认值 —— 所有字段的**唯一**定义处
# ----------------------------------------------------------------------

DEFAULTS: dict = {
    # 浮窗几何
    'x': 3000, 'y': 60, 'w': 470, 'h': 580,
    # 显示
    'opacity': 92,          # 浮窗不透明度 %
    'bg_alpha': 100,        # 底板浓度 %
    'preview': 5,           # 预告几个音
    # 谱面
    'last_sheet': '',
    # 浮窗开关
    'overlay_show': True,
    'only_game': False,
    # ★ `handle` 字段已删除 ★ —— 用户：「这个选项可以去掉，那个地方
    #   需要经常显示的」。拖动手柄现在常驻，没有开关可存。
    #   老配置里剩下的那个键会被 `normalise()` 丢掉（不在 DEFAULTS 里）。
    'follow_editor': True,
    # ★ 播放声音 ★ —— 用户：「多一个选项，播放声音」
    #   浮窗按谱面时间往前走的时候，同步把琴音放出来。
    #   默认**关**：以前一直是没声音的，升级完突然自己响起来更吓人。
    'sound': False,
    # ★ 透视贴合：已删除 ★
    #   这里原来存着 `fit_on` / `fit_quad`（屏幕坐标的四角）/ `fit_us` /
    #   `fit_vs`（内部分界表）。用户后来把整套贴合拿掉了
    #   （「自动贴合也删掉，可以调整大小和位置就行了」）——
    #   浮窗位置和大小就靠上面的 `x/y/w/h`。
    #   老配置里剩下的那几个键会被 `normalise()` 丢掉（不在 DEFAULTS 里）。
    # 热键（`'不绑定'` 是 HotkeyEdit 认得的一种取值）
    'hk_play': '不绑定',
    'hk_restart': '不绑定',
    'hk_toggle': '不绑定',
    'countdown': 3,
}

# 需要钳到整数区间的字段：(最小, 最大)
_RANGES = {
    'x': (-4000, 8000), 'y': (-4000, 8000),
    'w': (180, 3000), 'h': (180, 3000),
    'opacity': (5, 100), 'bg_alpha': (0, 100),
    # ★ `preview` 的上限从 12 收到 5 ★
    #   用户：「这里目前最大 5，多了没有用」。
    #   ★ 改这里的时候，`control_build.py` 里 `sp_preview.setRange()`
    #     必须跟着改 ★ —— 两处都是"2~12"的话，配置文件里存的老值
    #     （比如 8）读进来不会被这一层挡住，只会被控件悄悄钳到 5，
    #     看起来能用，实际是两个地方各说各话。
    'preview': (2, 5), 'countdown': (0, 15),
}

# 布尔字段
_BOOLS = ('overlay_show', 'only_game',
          'follow_editor', 'sound')

# 字符串字段
_STRINGS = ('last_sheet',)



def _as_int(v, key: str):
    lo, hi = _RANGES.get(key, (None, None))
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = int(DEFAULTS.get(key, 0))
    if lo is not None:
        n = max(lo, min(hi, n))
    return n


def _as_bool(v, key: str) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ('', '0', 'false', 'no', 'off')
    return bool(v)


def _as_str(v, key: str) -> str:
    if v is None:
        return str(DEFAULTS.get(key, ''))
    if isinstance(v, (list, tuple, dict)):
        # 热键那种结构化取值交给调用方解析，这里只保证不崩
        return v
    return str(v)


def normalise(raw) -> dict:
    """把任意来源的 dict 规整成"字段齐全、类型正确、取值在范围内"的配置。

    ★ 配置文件是用户能直接拿记事本改的 ★
      所以这里不能假设类型对：`"w": "470"`、`"dedup": "false"`、
      `"opacity": 999` 都可能出现。逐个纠正比在几百行 UI 代码里
      到处写 `int()` / `bool()` 稳得多。
    """
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        raw = {}
    for k, v in raw.items():
        if k not in DEFAULTS:
            continue                      # 忽略已经删掉的旧字段
        if k in _RANGES:
            cfg[k] = _as_int(v, k)
        elif k in _BOOLS:
            cfg[k] = _as_bool(v, k)
        elif k in _STRINGS:
            cfg[k] = _as_str(v, k)
        else:
            cfg[k] = v                    # 热键那类原样留着
    _migrate(cfg)
    return cfg


def _migrate(cfg: dict) -> None:
    """老取值的迁移。"""
    # 旧字段一概不用管：`pin` / `pin_x` / `pin_y` / `cast_file`、
    # 以及随「跟随画面 / 听音」一起删掉的 `engine`、`live_sense`、
    # `dedup`、`gate`、`sense`、`live_device`、`listen_device`、
    # `follow_motion`、`hk_listen`、`hk_follow` ——
    # `normalise` 的"不在 DEFAULTS 里就忽略"已经把它们挡掉了，
    # 写回时自然就没了。


def load(path: str | None = None) -> dict:
    """读配置。文件不在 / 读坏了 / 是半截 JSON，都退回默认值，绝不抛。"""
    p = path or config_path()
    raw = {}
    try:
        if os.path.isfile(p):
            with open(p, 'r', encoding='utf-8') as f:
                raw = json.load(f)
    except Exception:
        raw = {}
    return normalise(raw)


def save(cfg: dict, path: str | None = None) -> bool:
    """写配置。**先写临时文件再替换** —— 中途崩了不会留下半截 JSON。

    （以前是直接 `open(p, 'w')` + `json.dump`：写到一半断电 / 崩溃，
      那份配置就废了，下次启动所有设置回默认。）
    """
    p = path or config_path()
    data = normalise(cfg)
    try:
        d = os.path.dirname(os.path.abspath(p)) or '.'
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.cfg-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)            # 同盘 replace 是原子的
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        return False
