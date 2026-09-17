# -*- coding: utf-8 -*-
"""核心逻辑测试 —— 验收标准全部取自猫弹琴 README 里的时值公式。"""

import math

import pytest

from core import edit_model, encode, layout, parser, timeline


# ---------------- 时值：先加法，后乘法 ----------------

@pytest.mark.parametrize('tok, beats, rest', [
    # README 常用时值表
    ('1', 1.0, False),          # 四分音符
    ('1-', 2.0, False),         # 二分音符
    ('1--', 3.0, False),
    ('^1', 0.5, False),         # 八分音符
    ('^^1', 0.25, False),       # 十六分音符
    ('^1--', 1.5, False),       # 附点四分 (1+2)*0.5
    ('^^1--', 0.75, False),     # 附点八分 (1+2)*0.5*0.5
    # 休止符
    ('-', 1.0, True),
    ('^-', 0.5, True),
    ('^^-', 0.25, True),
    # 音高花样
    ("3'", 1.0, False),
    ("1''", 1.0, False),
    ('#4', 1.0, False),
    ('6.', 1.0, False),
])
def test_token_duration(tok, beats, rest):
    dur, is_rest = parser.token_duration(tok)
    assert math.isclose(dur, beats, abs_tol=1e-9), \
        '%s 时长应为 %s，实得 %s' % (tok, beats, dur)
    assert is_rest is rest


def test_duration_add_before_multiply():
    """关键是顺序：先加后乘，不能反过来。"""
    # (1+2)*0.5 = 1.5  —— 若是先乘后加会得到 1+2*0.5=2.0
    assert math.isclose(parser.token_duration('^1--')[0], 1.5)


# ---------------- 和弦 ----------------

def test_chord_plain():
    s = parser.parse("1'&3'&5'")
    assert len(s.chords) == 1
    c = s.chords[0]
    assert c.pitches == ["1'", "3'", "5'"]
    assert math.isclose(c.duration, 1.0)
    assert not c.is_rest


def test_chord_with_rhythm():
    s = parser.parse('^1&3&5')
    c = s.chords[0]
    assert c.pitches == ['1', '3', '5']
    assert math.isclose(c.duration, 0.5)


# ---------------- 变速 ----------------

def test_bpm_marker():
    s = parser.parse('#120# 1 2 3')
    assert isinstance(s.events[0], parser.BpmChange)
    assert s.events[0].bpm == 120
    assert len(s.chords) == 3


def test_bpm_sharp_note_not_confused():
    """#4 是半音，不能被当成变速标记。"""
    s = parser.parse('#4 5')
    assert len(s.chords) == 2
    assert s.chords[0].pitches == ['#4']
    bpm_events = [e for e in s.events if isinstance(e, parser.BpmChange)]
    assert bpm_events == []


def test_bpm_change_midway():
    s = parser.parse('#120# 1 #180# 2')
    bpms = [e.bpm for e in s.events if isinstance(e, parser.BpmChange)]
    assert bpms == [120, 180]


# ---------------- 前置 ^ 修饰符 ----------------

def test_pending_caret_prefix():
    """`^` 单独成 token 时，要拼到下一个音符前面。"""
    s = parser.parse('^^ 1')
    assert len(s.chords) == 1
    assert math.isclose(s.chords[0].duration, 0.25)


# ---------------- 非乐谱文字 ----------------

def test_non_score_text_skipped():
    s = parser.parse('小星星\n1 1 5 5')
    assert s.title == '小星星'
    assert len(s.chords) == 4


def test_non_score_text_leaves_no_gap():
    """跳过文字不能留下时间空档 —— 拍数总和应与纯音符一致。"""
    a = parser.parse('1 2 3')
    b = parser.parse('随便写点啥\n1 2 3\n再来一行废话')
    assert math.isclose(sum(c.duration for c in a.chords),
                        sum(c.duration for c in b.chords))


def test_whitespace_ignored():
    a = parser.parse('1 2 3')
    b = parser.parse('  1\n\n2\t\t3  ')
    assert len(a.chords) == len(b.chords) == 3


# ---------------- 键位表 ----------------

def test_pad_mapping():
    assert layout.pitch_to_pad('1') == 1
    assert layout.pitch_to_pad('4') == 4
    assert layout.pitch_to_pad('5') == 5
    assert layout.pitch_to_pad('8') == 8
    assert layout.pitch_to_pad("1'") == 9
    assert layout.pitch_to_pad("4'") == 12
    assert layout.pitch_to_pad("5'") == 13
    assert layout.pitch_to_pad("1''") == 16


def test_pad_mapping_missing_pitch():
    """低音不在琴上。"""
    assert layout.pitch_to_pad('6.') is None
    assert layout.unmapped_pitches(['1', '6.', '2', '6.']) == ['6.']


def test_pad_count():
    assert len(layout.all_pitches()) == 16
    assert len(set(layout.all_pitches())) == 16


# ---------------- 时间轴 ----------------

def test_timeline_basic():
    s = parser.parse('#60# 1 1')       # 60 BPM → 1 拍 = 1 秒
    t = timeline.Timeline(s)
    assert len(t) == 2
    assert math.isclose(t.items[0].start_sec, 0.0)
    assert math.isclose(t.items[0].end_sec, 1.0)
    assert math.isclose(t.items[1].start_sec, 1.0)
    assert math.isclose(t.total_sec, 2.0)


def test_timeline_bpm_change():
    s = parser.parse('#60# 1 #120# 1')
    t = timeline.Timeline(s)
    assert math.isclose(t.items[0].duration_sec, 1.0)
    assert math.isclose(t.items[1].duration_sec, 0.5)
    assert math.isclose(t.total_sec, 1.5)
    assert t.bpm_at(0.0) == 60
    assert t.bpm_at(1.0) == 120


def test_timeline_lookup():
    s = parser.parse('#60# 1 2 3')
    t = timeline.Timeline(s)
    assert t.index_at(-1) == -1
    assert t.index_at(0.0) == 0
    assert t.index_at(0.99) == 0
    assert t.index_at(1.0) == 1
    assert t.index_at(2.5) == 2
    assert t.index_at(999) == 2
    up = t.upcoming(1.0, 2)
    assert [x.index for x in up] == [1, 2]


def test_triplet_trick_from_readme():
    """README 的三连音技巧：#180# 下弹 3 个 ^，在 120 拍感里正好 0.5 秒。"""
    s = parser.parse('#180# ^1 ^2 ^3')
    t = timeline.Timeline(s)
    assert math.isclose(t.total_sec, 0.5, abs_tol=1e-6)


def test_rest_takes_time():
    """休止符要占时间。"""
    s = parser.parse('#60# 1 - 1')
    t = timeline.Timeline(s)
    assert len(t) == 3
    assert t.items[1].chord.is_rest
    assert math.isclose(t.total_sec, 3.0)
    assert math.isclose(t.items[2].start_sec, 2.0)


def test_empty_sheet():
    s = parser.parse('')
    assert not s
    t = timeline.Timeline(s)
    assert not t
    assert t.index_at(0) == -1
    assert t.upcoming(0) == []
    assert t.stats() == '空谱'


# ---------------- 开头 BPM 必须覆盖默认值（曾经是 bug） ----------------

def test_bpm_at_start_overrides_default():
    """曲子开头写 #100#，生效 BPM 必须是 100，不能还留着默认的 120。"""
    s = parser.parse('#100# 1 1')
    t = timeline.Timeline(s)
    assert t.bpm_points[0] == (0.0, 100)
    assert t.bpm_at(0.0) == 100
    assert 'BPM 100' in t.stats()
    # 100 BPM → 1 拍 = 0.6 秒
    assert math.isclose(t.items[0].duration_sec, 0.6)


def test_bpm_points_dedup_at_same_time():
    """同一时间点只留一个 BPM，别出现 (0,120)(0,100)。"""
    s = parser.parse('#100# 1')
    t = timeline.Timeline(s)
    assert len(t.bpm_points) == 1


def test_no_bpm_falls_back_to_default():
    s = parser.parse('1 1')
    t = timeline.Timeline(s)
    assert t.bpm_points == [(0.0, 120)]
    assert t.bpm_at(0.0) == 120


def test_demo_sheet_loads():
    """仓库里的示例谱面要能正常解析。"""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'sheets', 'demo.txt')
    if not os.path.isfile(path):
        pytest.skip('示例谱面不存在')
    sheet = parser.load(path)
    assert sheet
    assert sheet.title == '小星星'
    t = timeline.Timeline(sheet)
    assert len(t) == 42
    assert t.bpm_points[0][1] == 100


# ---------------- 反向编译：拍数 -> 记谱写法 ----------------

@pytest.mark.parametrize('beats, expect', [
    (1.0, '1'),
    (0.5, '^1'),
    (0.25, '^^1'),
    (0.125, '^^^1'),
    (2.0, '1-'),
    (3.0, '1--'),
    (1.5, '^1--'),
    (0.75, '^^1--'),
    (1.25, '^^1----'),
])
def test_encode_note(beats, expect):
    assert encode.token_for(beats, '1') == expect


@pytest.mark.parametrize('beats, expect', [
    (1.0, '-'),
    (0.5, '^-'),
    (0.25, '^^-'),
    (2.0, '--'),
])
def test_encode_rest(beats, expect):
    assert encode.encode_rest(beats) == expect


@pytest.mark.parametrize('beats', [
    0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0,
])
def test_encode_roundtrip(beats):
    """编码出来的写法，解析回去必须还是同一个拍数。"""
    tok = encode.token_for(beats, '1')
    dur, is_rest = parser.token_duration(tok)
    assert not is_rest
    assert math.isclose(dur, beats, abs_tol=1e-9), \
        '%s 拍 -> %s -> %s 拍' % (beats, tok, dur)

    rest_tok = encode.encode_rest(beats)
    dur2, is_rest2 = parser.token_duration(rest_tok)
    assert is_rest2
    assert math.isclose(dur2, beats, abs_tol=1e-9), \
        '休止 %s 拍 -> %s -> %s 拍' % (beats, rest_tok, dur2)


def test_encode_roundtrip_through_full_parse():
    """拼成一整段谱子，解析回来时间轴要一致。"""
    beats_seq = [1.0, 0.5, 0.5, 1.25, 0.75, 2.0]
    text = '#120# ' + ' '.join(encode.token_for(b, '1')
                              for b in beats_seq)
    sheet = parser.parse(text)
    assert len(sheet.chords) == len(beats_seq)
    t = timeline.Timeline(sheet)
    assert math.isclose(t.total_beats, sum(beats_seq), abs_tol=1e-9)


def test_split_gap():
    """长空档要拆成多个休止符，总拍数不变。"""
    parts = encode.split_gap(5.5)
    total = 0.0
    for p in parts:
        d, rest = parser.token_duration(p)
        assert rest
        total += d
    assert math.isclose(total, 5.5, abs_tol=1e-9)


# ---------------- 可编辑模型：拖动改间距 ----------------

def test_edit_model_build():
    m = edit_model.EditModel(parser.parse('1 2 3'))
    assert len(m.notes) == 3
    assert math.isclose(m.total_beats, 3.0)
    assert m.rebuild() == '1 2 3'


def test_move_note_inserts_rest():
    """把第 2 个音往右挪 1 拍 —— 应该插进一个休止符，后面的音跟着走。"""
    m = edit_model.EditModel(parser.parse('1 2 3'))
    m.move_note(1, 1.0)
    t = timeline.Timeline(parser.parse(m.rebuild()))
    # 休止符也占一个 item，所以只看有声的音
    audio = [it for it in t.items if not it.chord.is_rest]
    assert len(audio) == 3
    assert math.isclose(audio[0].start_beat, 0.0)
    assert math.isclose(audio[1].start_beat, 2.0)
    assert math.isclose(audio[2].start_beat, 3.0)
    # 中间确实多了一个休止符
    assert sum(1 for it in t.items if it.chord.is_rest) == 1


def test_move_note_back_removes_rest():
    m = edit_model.EditModel(parser.parse('1 - 2 3'))
    assert math.isclose(m.gap_before(2), 1.0)
    m.move_note(2, -1.0)
    t = timeline.Timeline(parser.parse(m.rebuild()))
    assert len(t.items) == 3          # 休止符被吃掉了
    assert math.isclose(t.items[1].start_beat, 1.0)


def test_move_cannot_go_negative():
    m = edit_model.EditModel(parser.parse('1 2'))
    m.move_note(1, -5.0)
    t = timeline.Timeline(parser.parse(m.rebuild()))
    assert math.isclose(t.items[1].start_beat, 1.0)


def test_rebuild_preserves_original_tokens():
    """原始写法（和弦、附点）回写时一个字节都不能变。"""
    src = "1'&3'&5' ^^1-- 2"
    m = edit_model.EditModel(parser.parse(src))
    assert m.rebuild() == src


def test_bpm_marks_survive_rebuild():
    m = edit_model.EditModel(parser.parse('#120# 1 #180# 2'))
    out = m.rebuild()
    bpms = [e.bpm for e in parser.parse(out).events
            if isinstance(e, parser.BpmChange)]
    assert bpms == [120, 180]


def test_drag_changes_total_time():
    m = edit_model.EditModel(parser.parse('#60# 1 2 3'))
    t0 = timeline.Timeline(parser.parse(m.rebuild()))
    m.move_note(2, 2.0)
    t1 = timeline.Timeline(parser.parse(m.rebuild()))
    assert math.isclose(t1.total_sec - t0.total_sec, 2.0)


def test_remove_note():
    m = edit_model.EditModel(parser.parse('1 2 3'))
    m.remove_note(1)
    t = timeline.Timeline(parser.parse(m.rebuild()))
    assert len(t.items) == 2
