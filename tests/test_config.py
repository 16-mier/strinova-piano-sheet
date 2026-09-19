# -*- coding: utf-8 -*-
"""`ui/config.py` 的单元测试。

★ 这个文件本身就是这次拆分的收益 ★
  以前 `_load_config` / `_save_config` 长在 `ControlWindow` 里，
  要测"配置文件里写了个字符串 `"470"` 会怎样"，
  得先造一个 QMainWindow、把几百个控件都建出来。
  现在给一份 dict 就能测。

测的都是**真实会遇到的**情况：配置文件是用户能拿记事本直接改的，
所以类型错、超范围、字段被删、JSON 被写坏，都得有确定行为。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui import config                                      # noqa: E402


# ----------------------------------------------------------------------
# 默认值与规整
# ----------------------------------------------------------------------

def test_missing_file_returns_defaults(tmp_path):
    cfg = config.load(str(tmp_path / 'nope.json'))
    assert cfg == config.normalise({})
    assert cfg['preview'] == 5
    assert cfg['w'] == 470 and cfg['h'] == 580


def test_corrupt_json_falls_back_instead_of_raising(tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text('{ 这不是 JSON', encoding='utf-8')
    cfg = config.load(str(p))          # 不能抛
    assert cfg['preview'] == 5


def test_structured_value_for_string_field_never_leaks_out():
    """`last_sheet` 必须是字符串 —— 否则主窗口根本起不来。

    ★ 这两条测试是从一个真崩溃里长出来的 ★
      `_as_str()` 原来对 list / tuple / dict **原样返回**，理由是
      "热键那种结构化取值交给调用方解析"。可热键走的是另一个分支。

      结果：用户在 config.json 里把 `"last_sheet"` 写成 `{}`，
      dict 一路漏到 `os.path.isfile()` → TypeError → 而那次调用发生在
      `ControlWindow.__init__` 里、`main.py` 没有 try →
      **双击图标没反应，窗口永远不出现**。

      改一个文本文件就能把程序锁死，所以这里逐个钉死。
    """
    for bad in ({}, [], ['a'], {'a': 1}, set(), 0, 1.5, True, None):
        cfg = config.normalise({'last_sheet': bad})
        assert isinstance(cfg['last_sheet'], str), '漏出了 %r' % (bad,)


def test_huge_number_falls_back_instead_of_raising():
    """`1e400` 读出来是 `inf`，而 `int(inf)` 抛的是 OverflowError。

    它不是 TypeError / ValueError —— 只接那两个的话这条会漏过去，
    后果跟上面一样是"窗口打不开"。
    """
    cfg = config.normalise({'w': 1e400, 'opacity': float('inf'),
                            'countdown': float('nan')})
    assert cfg['w'] == 470             # 全部退回默认值
    assert cfg['opacity'] == 92
    assert cfg['countdown'] == 3


def test_hotkey_structured_values_still_pass_through():
    """热键那类结构化取值该原样留着 —— 别被上面那次类型硬化误伤。

    它们不在 `_STRINGS` / `_RANGES` / `_BOOLS` 任何一个里，
    走的是 `normalise()` 最后那个 `else` 分支。
    """
    cfg = config.normalise({'hk_play': [1, 65], 'hk_restart': '不绑定'})
    assert cfg['hk_play'] == [1, 65]
    assert cfg['hk_restart'] == '不绑定'


def test_wrong_types_are_coerced():
    # （`handle` 字段已经删了 —— 拖动手柄改成常驻，用户：
    #   「这个选项可以去掉，那个地方需要经常显示的」。
    #   这里换个还在的布尔字段来测"字符串 'false' 要认成假"。）
    cfg = config.normalise({'w': '470', 'opacity': '88', 'only_game': 'false'})
    assert cfg['w'] == 470 and isinstance(cfg['w'], int)
    assert cfg['opacity'] == 88
    assert cfg['only_game'] is False


def test_out_of_range_is_clamped():
    cfg = config.normalise({'opacity': 999, 'w': 5, 'x': -99999,
                            'preview': 100})
    assert cfg['opacity'] == 100
    assert cfg['w'] == 180               # 下界
    assert cfg['x'] == -4000
    # ★ `preview` 的上限从 12 收到 5 ★
    #   用户：「这里目前最大 5，多了没有用」——
    #   §16.58 之后圈只提前 `lead` 秒出现（上限 1.2 秒），
    #   排在第 6 个往后的音根本没有圈可看，只有一块越来越淡的预告格。
    #   改上限时 `control_build.py` 的 `sp_preview.setRange()` 要一起改，
    #   两边对不上会出现"控件显示 5、配置里存着 8"这种各说各话。
    assert cfg['preview'] == 5


def test_garbage_in_numeric_field_uses_default():
    cfg = config.normalise({'w': '宽一点', 'preview': None, 'countdown': []})
    assert cfg['w'] == config.DEFAULTS['w']
    assert cfg['preview'] == config.DEFAULTS['preview']
    assert cfg['countdown'] == config.DEFAULTS['countdown']


def test_unknown_keys_are_dropped():
    """已经删掉功能留下的旧字段，不该继续攒在文件里。"""
    cfg = config.normalise({'pin': True, 'pin_x': 0.5, 'cast_file': '/tmp/x'})
    assert 'pin' not in cfg and 'pin_x' not in cfg and 'cast_file' not in cfg


def test_hotkey_structured_value_passes_through():
    """热键存的是 `[mods, vk]`，规整层不能把它压成字符串。"""
    cfg = config.normalise({'hk_play': [0, 97]})
    assert cfg['hk_play'] == [0, 97]
    cfg2 = config.normalise({'hk_play': '不绑定'})
    assert cfg2['hk_play'] == '不绑定'


# ----------------------------------------------------------------------
# 迁移
# ----------------------------------------------------------------------

def test_fields_of_removed_features_are_dropped():
    """★ 「跟随画面 / 听音」删掉之后，它们留下的字段不该继续攒在文件里 ★

      （`engine` / `live_sense` / `dedup` / `gate` / `sense` /
        `live_device` / `listen_device` / `follow_motion` /
        `hk_listen` / `hk_follow`）
      `normalise()` 的"不在 DEFAULTS 里就忽略"负责这件事 ——
      写回时自然就没了，不用专门写迁移代码。
    """
    cfg = config.normalise({'engine': 'cnmf', 'live_sense': 70,
                            'dedup': False, 'gate': False, 'sense': 2,
                            'live_device': 'x', 'listen_device': 'y',
                            'follow_motion': True,
                            'hk_listen': 'F7', 'hk_follow': 'F8'})
    for k in ('engine', 'live_sense', 'dedup', 'gate', 'sense',
              'live_device', 'listen_device', 'follow_motion',
              'hk_listen', 'hk_follow'):
        assert k not in cfg, '%s 应该被丢掉' % k


# ----------------------------------------------------------------------
# 落盘
# ----------------------------------------------------------------------

def test_roundtrip(tmp_path):
    p = str(tmp_path / 'cfg.json')
    src = config.normalise({'w': 600, 'preview': 9, 'only_game': False,
                            'hk_play': [1, 98]})
    assert config.save(src, p)
    back = config.load(p)
    for k in ('w', 'preview', 'only_game', 'hk_play'):
        assert back[k] == src[k], k


def test_save_leaves_no_temp_file(tmp_path):
    """原子写：临时文件必须被替换掉，不能留在磁盘上。"""
    p = str(tmp_path / 'cfg.json')
    config.save({'w': 500}, p)
    left = [n for n in os.listdir(str(tmp_path)) if n.startswith('.cfg-')]
    assert left == [], '留下了临时文件：%s' % left


def test_save_never_leaves_half_written_json(tmp_path):
    """写到一半崩掉不该毁掉原配置 —— 所以是"先写临时再替换"。"""
    p = str(tmp_path / 'cfg.json')
    config.save({'w': 500, 'preview': 7}, p)
    before = open(p, encoding='utf-8').read()
    # 传一个 json 序列化不了的东西，逼它在中途失败
    ok = config.save({'w': 500, 'hk_play': object()}, p)
    assert ok is False, '本该失败却报了成功'
    assert open(p, encoding='utf-8').read() == before, '原文件被破坏了'
    assert json.loads(before)['w'] == 500


def test_saved_file_is_readable_utf8_json(tmp_path):
    p = str(tmp_path / 'cfg.json')
    config.save({'last_sheet': 'C:/谱面/小星星.txt'}, p)
    with open(p, encoding='utf-8') as f:
        got = json.load(f)
    assert got['last_sheet'] == 'C:/谱面/小星星.txt'      # 别被转义成 \uXXXX
