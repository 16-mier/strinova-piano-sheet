# -*- coding: utf-8 -*-
"""核心逻辑测试 —— 验收标准全部取自猫弹琴 README 里的时值公式。"""

import math

import pytest

from core import (edit_model, encode, layout, parser, timeline,
                  transcribe)


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


# ---------------- 区域操作（框选 / 删除 / 插入） ----------------

def _audio(m):
    t = timeline.Timeline(parser.parse(m.rebuild()))
    return [it for it in t.items if not it.chord.is_rest]


def test_notes_in_range():
    # 1 占 0~1 拍，2 占 1~2，3 占 2~3，4 占 3~4
    # 区间 [1.0, 2.5) 只跟 2 和 3 重叠
    m = edit_model.EditModel(parser.parse('1 2 3 4'))
    got = m.notes_in_range(1.0, 2.5)
    assert [n.raw for n in got] == ['2', '3']


def test_remove_range_ripples():
    """删掉中间一段，后面的要往前接上。"""
    m = edit_model.EditModel(parser.parse('1 2 3 4'))
    n = m.remove_range(1.0, 3.0)
    assert n == 2
    audio = _audio(m)
    assert [it.chord.pitches[0] for it in audio] == ['1', '4']
    assert math.isclose(audio[1].start_beat, 1.0)


def test_remove_range_partial():
    """只要跟区间有重叠就会被删（按重叠算，不是包含）。"""
    m = edit_model.EditModel(parser.parse('1 2 3'))
    # [1.5, 2.5) 同时压到 2（1~2）和 3（2~3）
    n = m.remove_range(1.5, 2.5)
    assert n == 2
    assert [it.chord.pitches[0] for it in _audio(m)] == ['1']


def test_clear_all():
    m = edit_model.EditModel(parser.parse('1 2 3'))
    m.clear_all()
    assert not m.notes
    assert m.total_beats == 0.0
    assert m.rebuild() == ''


def test_insert_tokens_at_position():
    m = edit_model.EditModel(parser.parse('1 3'))
    idx = m.rest_index_after_beat(1.0)
    m.insert_tokens(idx, ['2'])
    audio = _audio(m)
    assert [it.chord.pitches[0] for it in audio] == ['1', '2', '3']
    assert math.isclose(audio[1].start_beat, 1.0)


def test_insert_tokens_with_gap():
    """间距 2 拍 = 一个音 + 停一拍（音紧跟在前一个后面，停一拍之后才是下一个）。"""
    m = edit_model.EditModel(parser.parse('1'))
    m.insert_tokens(1, ['5', '-'])
    audio = _audio(m)
    assert len(audio) == 2
    assert math.isclose(audio[1].start_beat, 1.0)     # 5 紧跟在 1 后面
    assert math.isclose(m.total_beats, 3.0)           # 1 拍音 + 1 拍音 + 1 拍停
    t = timeline.Timeline(parser.parse(m.rebuild()))
    assert math.isclose(t.total_beats, 3.0)


def test_tokens_for_gap_sizes():
    from ui.editor import tokens_for
    assert tokens_for('1', 0.25) == ['^^1']
    assert tokens_for('1', 0.5) == ['^1']
    assert tokens_for('1', 1.0) == ['1']
    assert tokens_for('1', 2.0) == ['1', '-']
    assert tokens_for('1', 3.0) == ['1', '--']


def test_rest_index_after_beat():
    m = edit_model.EditModel(parser.parse('1 2 3'))
    assert m.rest_index_after_beat(0.0) == 0
    assert m.rest_index_after_beat(1.0) == 1
    assert m.rest_index_after_beat(99.0) == 3


# ---------------- 和弦（同一时刻一起响） ----------------

def test_split_and_make_raw():
    assert edit_model.split_raw("^^1'&3'--") == ('^^', "1'&3'", '--')
    assert edit_model.split_raw('1^') == ('', '1', '^')
    assert edit_model.split_raw('7') == ('', '7', '')
    assert edit_model.split_raw('1~-') == ('', '1', '~-')
    assert edit_model.make_raw('^^1--', ['1', '3']) == '^^1&3--'
    # ^ 写在后面对时长是等价的（parser 只看个数），所以原地保留
    assert edit_model.make_raw('1^', ['5', '6']) == '5&6^'
    assert edit_model.make_raw('^^1--', ['3', '1']) == '^^3&1--'


def test_add_pitch_sorts_by_pad():
    m = edit_model.EditModel(parser.parse('1'))
    n = m.notes[0]
    assert m.add_pitch(n, "1'")            # PAD9
    assert m.add_pitch(n, '5')             # PAD5
    assert n.pitches == ['1', '5', "1'"]   # 永远按琴上的顺序写
    assert n.raw == "1&5&1'"
    assert not m.add_pitch(n, '5')         # 重复的不加


def test_remove_pitch_keeps_block_until_empty():
    m = edit_model.EditModel(parser.parse('1&3'))
    n = m.notes[0]
    assert m.remove_pitch(n, '3')
    assert n.pitches == ['1']
    assert n.raw == '1'
    assert len(m.notes) == 1
    assert m.remove_pitch(n, '1')          # 拿光了 -> 整块删掉
    assert len(m.notes) == 0


def test_merge_into_prev_makes_chord():
    m = edit_model.EditModel(parser.parse('1 3'))
    got = m.merge_into_prev(1)
    assert got is m.notes[0]
    assert len(m.notes) == 1
    assert m.notes[0].pitches == ['1', '3']
    assert m.notes[0].raw == '1&3'
    assert math.isclose(m.total_beats, 1.0)   # 两个音挤在同一拍，总时长少一拍
    # 回写成文本之后，解析回来还得是同一个和弦
    again = parser.parse(m.rebuild())
    assert again.chords[0].pitches == ['1', '3']


def test_merge_keeps_longer_duration():
    """合并时时值取长的那个 —— 不然音乐会莫名其妙变快。"""
    m = edit_model.EditModel(parser.parse('1 3-'))
    m.merge_into_prev(1)
    assert m.notes[0].raw == '1&3-'
    assert math.isclose(m.notes[0].dur, 2.0)


def test_move_to_beat_snaps_into_chord():
    m = edit_model.EditModel(parser.parse('1 3'))
    second = m.notes[1]
    got = m.move_to_beat(second, 0.0)
    assert got is m.notes[0]              # 变成和弦之后，选中的是前面那个块
    assert len(m.notes) == 1
    assert m.notes[0].pitches == ['1', '3']


def test_move_to_beat_normal_move():
    m = edit_model.EditModel(parser.parse('1 3'))
    second = m.notes[1]
    got = m.move_to_beat(second, 3.0)
    assert got is second
    assert math.isclose(second.start, 3.0)
    assert math.isclose(m.total_beats, 4.0)


def test_move_to_beat_clamps_when_overshot():
    """拖过头压到前一个音身上（但没对准起点）= 紧贴着，不合并。"""
    m = edit_model.EditModel(parser.parse('1 3'))
    second = m.notes[1]
    got = m.move_to_beat(second, 0.5)
    assert got is second
    assert len(m.notes) == 2
    assert math.isclose(second.start, 1.0)      # 紧贴在前一个音后面


def test_split_pitch_out():
    m = edit_model.EditModel(parser.parse('1&3'))
    n = m.notes[0]
    out = m.split_pitch_out(n, '3')
    assert out is not None
    assert n.pitches == ['1']
    assert out.pitches == ['3']
    assert len(m.notes) == 2
    assert math.isclose(out.start, 1.0)         # 拆出来的排在和弦后面


def test_set_dur_of_reencodes():
    m = edit_model.EditModel(parser.parse('1'))
    n = m.notes[0]
    assert m.set_dur_of(n, 0.5)
    assert n.raw == '^1'
    assert math.isclose(n.dur, 0.5)
    assert math.isclose(m.total_beats, 0.5)


def test_note_starting_at():
    m = edit_model.EditModel(parser.parse('1 3'))
    assert m.note_starting_at(0.0) is m.notes[0]
    assert m.note_starting_at(1.05) is m.notes[1]
    assert m.note_starting_at(5.0) is None
    assert m.note_at(0.5) is m.notes[0]


def test_chord_survives_rebuild_roundtrip():
    m = edit_model.EditModel(parser.parse('1&3&5 2'))
    assert m.notes[0].pitches == ['1', '3', '5']
    sheet2 = parser.parse(m.rebuild())
    assert sheet2.chords[0].pitches == ['1', '3', '5']
    assert sheet2.chords[1].pitches == ['2']


# ---------------- 听音记谱 ----------------

def test_pitch_freq_table():
    # ★ 这台琴的 `1` 是 C3（130Hz），不是通常简谱的 C4 ——
    #   从 16 个游戏原始采样里量出来的，见 tools/check_notes.py。
    assert math.isclose(transcribe.pitch_freq('1'), 130.81, abs_tol=0.1)
    assert math.isclose(transcribe.pitch_freq('8'), 261.63, abs_tol=0.1)
    assert math.isclose(transcribe.pitch_freq("1'"), 261.63, abs_tol=0.1)
    assert math.isclose(transcribe.pitch_freq("1''"), 523.25, abs_tol=0.3)
    # 8 和 1' 在乐理上是同一个音高（游戏里是两个键）
    assert math.isclose(transcribe.pitch_freq('8'),
                        transcribe.pitch_freq("1'"), abs_tol=1e-6)


def test_key_freqs_cover_all_16():
    assert len(transcribe.KEY_FREQS) == 16


def test_nearest_pitch_matches():
    p, cents = transcribe.nearest_pitch(130.0)      # 这台琴的 `1` 是 C3
    assert p == '1'
    assert abs(cents) < 50
    p2, _ = transcribe.nearest_pitch(520.0)
    assert p2 in ("1''", '7\'')
    # 完全跑调的音会给出很大的偏差
    _p3, big = transcribe.nearest_pitch(50.0)
    assert abs(big) > 200


def test_transcribe_silence():
    import numpy as np
    tok, hits = transcribe.transcribe(
        np.zeros(24000, dtype=np.float32), 48000)
    assert tok == []
    assert hits == []


def test_transcribe_synthetic_sine():
    """自己合成「1 3 5」，认出来的必须是期望序列的前缀（可能少认几个）。

    注：偶尔会漏掉末尾的音 —— 见 DEVELOPMENT.md 的「已知问题」。
    这里只锁住"不会认错音、不会乱序"这个底线。
    """
    import numpy as np

    rate = 48000
    seq = ['1', '3', '5']
    spb = 0.5
    total = int(len(seq) * spb * rate) + rate
    audio = np.zeros(total, dtype=np.float64)
    for i, p in enumerate(seq):
        f = transcribe.pitch_freq(p)
        n = int(spb * rate * 0.7)
        t = np.arange(n) / rate
        audio[i * int(spb * rate):][:n] += (
            np.sin(2 * np.pi * f * t) * np.exp(-4.0 * t) * 0.8)

    _tok, hits = transcribe.transcribe(audio, rate, bpm=120, min_margin=0.0)
    got = [h.pitch for h in hits]
    assert len(got) >= 2, '只认出 %d 个: %s' % (len(got), got)
    assert got == seq[:len(got)], '认出 %s，期望是 %s 的前缀' % (got, seq[:len(got)])


def test_same_pitch_follows_previous_key():
    """`8` 和 `1'` 是同一个音高的两个键，识别时**跟着上一个音走**。

    它俩基频完全一样（都是 262Hz），物理上分不开。跟着上一次的选择走，
    同一首曲子里至少是统一的，不会一会儿 `8` 一会儿 `1'`。
    """
    import os

    import pytest

    from core import synth
    from core.paths import app_dir

    notes = synth.ensure_notes(os.path.join(app_dir(), 'assets', 'notes'))
    if '8' not in notes:
        pytest.skip('没有游戏原始采样，跳过')

    seg, _rate = _as_seg(notes['8'])
    plain = transcribe.match_key(seg, 48000)
    followed = transcribe.match_key(seg, 48000, prefer="1'")
    other = transcribe.match_key(seg, 48000, prefer='5')     # 不同音高，不该乱跟

    assert plain[0] == '8'                 # 没上下文时给 PAD 序号小的那个
    assert followed[0] == "1'"             # 上一个音是 1'，就跟着用 1'
    assert other[0] == '8'                 # 上一个音是 5（另一个音高）不影响
    # 两个名字指的是同一个音高
    assert math.isclose(transcribe.pitch_freq(plain[0]),
                        transcribe.pitch_freq(followed[0]), abs_tol=1e-6)


def test_note_samples_pitch_table():
    """把 16 个采样的实测基频打出来 —— 这是**待你在游戏里核对的清单**。

    ⚠ 这里故意**不校验键名**，因为实测发现：
        3.wav 和 3_up.wav 的基频都是 330Hz
        6.wav 和 6_up.wav 的基频都是 440Hz
        8.wav / 1_up.wav 的基频都是 261Hz
      ……也就是说这 16 个采样里**只有 8 种音高**，好几对是重复的。
      所以「文件名 ↔ 琴上哪个键」这层对应关系还没被证实，
      必须**在游戏里实际弹一遍**才能定案（见 DEVELOPMENT.md）。

    用 `python -m pytest tests -k pitch_table -s` 看完整表格。
    """
    import os

    import pytest

    from core import synth
    from core.paths import app_dir

    notes = synth.ensure_notes(os.path.join(app_dir(), 'assets', 'notes'))
    if len(notes) < 16:
        pytest.skip('没有游戏原始采样（assets/notes），跳过')

    print()
    print('  %-9s %-6s %8s' % ('采样文件', '实测', '基频'))
    print('  ' + '-' * 30)
    seen = {}
    for pitch, path in sorted(notes.items()):
        seg, _rate = _as_seg(path)
        got = transcribe.match_key(seg, 48000)
        assert got[0], '%s 认不出音高' % pitch
        f = round(transcribe.pitch_freq(got[0]))
        seen.setdefault(f, []).append(pitch)
        print('  %-9s %-6s %8d Hz' % (pitch, got[0], f))
    print('  ' + '-' * 30)
    print('  不同音高只有 %d 种：%s'
          % (len(seen), '  '.join('%dHz×%d' % (f, len(v))
                                  for f, v in sorted(seen.items()))))
    # 底线：至少得认出 8 种不同音高，不然说明识别整个坏掉了
    assert len(seen) >= 8, '只认出 %d 种音高，识别有问题' % len(seen)


def _as_seg(path, n: int = 12000):
    """读一个采样文件 -> (前 n 个样本, 采样率)。

    窗口取 0.25 秒：短窗口的频率分辨率太差（0.1 秒只有 10Hz），
    连 257 和 261 这种相邻峰都分不开。
    """
    import numpy as np

    from core import audio_io
    a, sr = audio_io.load_audio(path, target_sr=48000)
    if len(a) < n:
        a = np.pad(a, (0, n - len(a)))
    return a[:n], 48000


def test_chord_is_collapsed_to_strongest_note():
    """和弦（同时按多个键）目前只会认成最强的那一个 —— 已知限制，锁住行为。"""
    import numpy as np

    from core import synth
    import os
    from core.paths import app_dir

    notes = synth.ensure_notes(os.path.join(app_dir(), 'assets', 'notes'))
    if '1' not in notes or '5' not in notes:
        import pytest
        pytest.skip('没有游戏原始采样，跳过')

    a1, _ = _as_seg(notes['1'])
    a5, _ = _as_seg(notes['5'])
    mixed = a1 * 0.5 + a5 * 1.0          # 5 更响
    pitch, _f, _m = transcribe.match_key(mixed, 48000)
    # 不锁"认成哪个"：1 和 5 是纯五度（频率 2:3），谐波大面积重合，
    # 谁强谁弱本来就含糊。只锁"不会认成一个跟它俩都不相干的音"。
    assert pitch in ('1', '5', "1'", '8'), '认成了不相干的 %s' % pitch

    n1, _f1, m1 = transcribe.match_key(a1, 48000)
    n5, _f5, m5 = transcribe.match_key(a5, 48000)
    assert (n1, n5) == ('1', '5')         # 单独听的时候都是准的
