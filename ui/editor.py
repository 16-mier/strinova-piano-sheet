# -*- coding: utf-8 -*-
"""打谱器 —— 边弹边写，拖着改间距。

三块联动：
  左上 打击垫  点了就出声；开着「同步写入」时还会把音符打进文本
  右上 谱面文本 跟猫弹琴一样的记谱法；改了立刻重画时间轴
  下面 时间轴   钢琴卷帘，**左右拖动音符就能改它跟前一个音之间的间距**

拖动写回文本的方式很克制：只重新生成休止符，音符的原始写法
（`1'&3'`、`^^1--` 这些）一个字节都不动。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from core import encode, layout, parser, timeline
from core.edit_model import EditModel

from .edit_player import EditPlayer
from .keypad import KeyPad
from .timeline_edit import TimelineEditor

HELP = """记谱速查
音高  1~7 中音 ｜ #4 半音 ｜ 6. 低音 ｜ 3' 高音 ｜ 1'' 倍高音
节奏  基础 1 拍；每个 - 加 1 拍；每个 ^ 乘 0.5
      1-   = 2 拍      ^1   = 0.5 拍
      ^^1  = 0.25 拍   ^1-- = 1.5 拍
休止  单独写 - 是休止 1 拍；^- 是休止 0.5 拍
和弦  用 & 连起来：1'&3'&5'
变速  #120# 写在任意位置
排版  空格换行随意；不像乐谱的文字会被跳过
"""

SAMPLE = """小星星
#100#
1 1 5 5 6 6 5-
4 4 3 3 2 2 1-
"""

GAP_CHOICES = [
    ('1 拍', 1.0),
    ('半拍', 0.5),
    ('1/4 拍', 0.25),
    ('1.5 拍', 1.5),
    ('2 拍', 2.0),
    ('3 拍', 3.0),
    ('4 拍', 4.0),
]

CHORD_TOL = 0.2         # 播放头落在某个音起点 ±这个拍数内 = 叠成和弦
UNDO_MAX = 60           # 撤销栈存多少步


def tokens_for(pitch: str, gap: float) -> list[str]:
    """按「间距」生成要插入的 token 序列。

    间距 = 这个音开始到下一个音开始的拍数。
    音本身占 1 拍，多出来的部分用休止符补。
    """
    if gap >= 1.0 - 1e-9:
        out = [pitch]
        rest = gap - 1.0
        if rest > 1e-6:
            out.extend(encode.split_gap(rest))
        return out
    c, d, _ = encode.encode_duration(gap, is_rest=False)
    return ['^' * c + pitch + '-' * d]


class EditorDialog(QDialog):
    def __init__(self, path: str | None = None, parent=None):
        super().__init__(parent)
        self.path = path
        self.saved = False
        self.model: EditModel | None = None
        self._syncing = False
        self._undo: list[str] = []
        self._applying = False          # 正在回滚，别再记一笔

        self.setWindowTitle('打谱器')
        self.resize(1240, 840)

        # ================= 左上：打击垫 =================
        self.pad = KeyPad()
        self.pad.setFixedSize(300, 300)
        n = self.pad.load_notes()

        pad_box = QGroupBox('打击垫（点了就出声）')
        pb = QVBoxLayout(pad_box)
        pb.addWidget(self.pad, 0, Qt.AlignmentFlag.AlignHCenter)

        self.cmb_gap = QComboBox()
        for label, val in GAP_CHOICES:
            self.cmb_gap.addItem(label, val)
        self.chk_insert = QCheckBox('点击时同步写入谱面')
        self.chk_insert.setChecked(True)
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel('触发间距'))
        gap_row.addWidget(self.cmb_gap)
        gap_row.addWidget(self.chk_insert)
        gap_row.addStretch(1)
        pb.addLayout(gap_row)

        self.lbl_pad = QLabel('音源已就绪（合成音色）' if n else '音源载入失败')
        self.lbl_pad.setStyleSheet('color:#8f98ae;')
        pb.addWidget(self.lbl_pad)

        # ================= 右上：文本 =================
        self.text = QPlainTextEdit()
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(12)
        self.text.setFont(mono)

        quick = QHBoxLayout()
        for label, ins in [('^', '^'), ('-', '-'), ('&', '&'),
                           ("'", "'"), ('#', '#'),
                           ('#120#', '#120# '), ('空格', ' ')]:
            b = QPushButton(label)
            b.setFixedSize(44, 26)
            b.clicked.connect(lambda _=False, s=ins: self._insert(s))
            quick.addWidget(b)
        b_help = QPushButton('速查')
        b_help.setFixedSize(56, 26)
        b_help.clicked.connect(
            lambda: QMessageBox.information(self, '记谱速查', HELP))
        quick.addWidget(b_help)
        quick.addStretch(1)

        text_box = QGroupBox('谱面文本')
        tb = QVBoxLayout(text_box)
        tb.addLayout(quick)
        tb.addWidget(self.text, 1)

        top = QHBoxLayout()
        top.addWidget(pad_box, 0)
        top.addWidget(text_box, 1)

        # ================= 下面：时间轴 =================
        self.tl_edit = TimelineEditor()
        self.tl_scroll = QScrollArea()
        self.tl_scroll.setWidget(self.tl_edit)
        self.tl_scroll.setWidgetResizable(True)
        self.tl_scroll.setMinimumHeight(16 * 24 + 60)
        self.tl_scroll.setStyleSheet('QScrollArea{border:1px solid #2c3346;}')

        tl_box = QGroupBox('时间轴：拖音符挪位置，拖到前一个音头上就并成和弦'
                           '（同时响）· 拖右边缘改时长 · 右键拿掉一个音 · '
                           '按住红线拖播放头 · 空白处拖 = 框选')
        tlb = QVBoxLayout(tl_box)
        tlb.addWidget(self.tl_scroll, 1)

        self.btn_play = QPushButton('▶  从这里播')
        self.btn_play.setFixedHeight(30)
        self.btn_stop = QPushButton('⏹  停止')
        self.btn_stop.setFixedHeight(30)
        self.btn_home = QPushButton('⏮  回到开头')
        self.btn_home.setFixedHeight(30)

        self.btn_play_sel = QPushButton('▶  听到选区结尾')
        self.btn_del_sel = QPushButton('🗑  删除选区')
        self.btn_sel_all = QPushButton('全选')
        self.btn_clear_sel = QPushButton('取消选区')
        self.btn_undo = QPushButton('↶  撤销')
        self.btn_undo.setToolTip('按 Ctrl+Z 也行（焦点在时间轴上时）')
        self.btn_clear_all = QPushButton('全删')
        for b in (self.btn_play_sel, self.btn_del_sel, self.btn_sel_all,
                  self.btn_clear_sel, self.btn_undo, self.btn_clear_all):
            b.setFixedHeight(30)
        self.btn_clear_all.setStyleSheet(
            'QPushButton{color:#ff9a9a;}')

        self.lbl_pos = QLabel('位置：第 0 拍')
        self.lbl_pos.setStyleSheet('color:#9aa3b8;')

        rowp = QHBoxLayout()
        rowp.addWidget(self.btn_play)
        rowp.addWidget(self.btn_stop)
        rowp.addWidget(self.btn_home)
        rowp.addSpacing(10)
        rowp.addWidget(self.btn_play_sel)
        rowp.addWidget(self.btn_del_sel)
        rowp.addSpacing(10)
        rowp.addWidget(self.btn_sel_all)
        rowp.addWidget(self.btn_clear_sel)
        rowp.addWidget(self.btn_undo)
        rowp.addWidget(self.btn_clear_all)
        rowp.addSpacing(10)
        rowp.addWidget(self.lbl_pos, 1)
        tlb.addLayout(rowp)

        # 写入位置：打击垫敲的音符落在哪儿
        self.cmb_write = QComboBox()
        self.cmb_write.addItem('写入位置：时间轴播放头', 'head')
        self.cmb_write.addItem('写入位置：谱面文本光标', 'cursor')
        self.cmb_write.setToolTip(
            '时间轴播放头：在下面时间轴上点一下定位，再敲打击垫，'
            '音符就插在那儿\n'
            '　★ 如果那一拍已经有音了，敲下去就是**叠成和弦**（一起响），'
            '不会占新的时间\n'
            '谱面文本光标：跟你手写文本一样，插在光标处')
        rowq = QHBoxLayout()
        rowq.addWidget(self.cmb_write)
        self.lbl_sel = QLabel('没有选区')
        self.lbl_sel.setStyleSheet('color:#7f8aa3;')
        rowq.addSpacing(12)
        rowq.addWidget(self.lbl_sel, 1)
        tlb.addLayout(rowq)

        # ================= 底部 =================
        self.info = QLabel('')
        self.info.setWordWrap(True)
        self.info.setStyleSheet('color:#9aa3b8;')

        self.btn_save = QPushButton('保存')
        self.btn_save_as = QPushButton('另存为…')
        self.btn_sample = QPushButton('插入示例')
        self.btn_close = QPushButton('关闭')
        for b in (self.btn_save, self.btn_save_as):
            b.setStyleSheet('QPushButton{font-weight:bold;padding:6px 18px;}')
        bottom = QHBoxLayout()
        bottom.addWidget(self.info, 1)
        bottom.addWidget(self.btn_sample)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_save_as)
        bottom.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        root.addLayout(top, 0)
        root.addWidget(tl_box, 1)
        root.addLayout(bottom)

        # ================= 播放器 =================
        self.player = EditPlayer(self)
        self.player.note_fired.connect(self.pad.flash)
        self.player.note_fired.connect(self.pad.player.play)
        self.player.tick.connect(self._on_player_tick)
        self.player.finished.connect(self._on_player_done)

        # 打击垫闪烁的时钟
        self._blink = QTimer(self)
        self._blink.setInterval(40)
        self._blink.timeout.connect(self.pad.tick)
        self._blink.start()

        # 文本防抖
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._refresh_from_text)

        self._wire()

        if path and os.path.isfile(path):
            self.load_file(path)
        else:
            self.text.setPlainText(SAMPLE)
        self._refresh_from_text()

    # ---------------- 信号 ----------------

    def _wire(self):
        self.pad.note_clicked.connect(self._on_pad_click)
        self.text.textChanged.connect(self._on_text_changed)
        self.tl_edit.changed.connect(self._on_timeline_changed)
        self.tl_edit.playhead_moved.connect(self._on_playhead)
        self.tl_edit.playhead_dropped.connect(self._on_playhead_dropped)
        self.tl_edit.seek_requested.connect(self._play_from_beat)
        self.tl_edit.undo_requested.connect(self._push_undo)

        self.btn_play.clicked.connect(
            lambda: self._play_from_beat(self.tl_edit.playhead))
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_home.clicked.connect(
            lambda: (self.tl_edit.set_playhead(0.0), self._on_playhead(0.0)))

        self.btn_play_sel.clicked.connect(self._play_selection)
        self.btn_del_sel.clicked.connect(self._delete_selection)
        self.btn_sel_all.clicked.connect(self.tl_edit.select_all)
        self.btn_clear_sel.clicked.connect(self.tl_edit.clear_selection)
        self.btn_undo.clicked.connect(self._undo_once)
        self.btn_clear_all.clicked.connect(self._clear_all)
        self.tl_edit.selection_changed.connect(self._update_sel_label)
        self.btn_save.clicked.connect(self.save)
        self.btn_save_as.clicked.connect(self.save_as)
        self.btn_sample.clicked.connect(
            lambda: self.text.setPlainText(SAMPLE))
        self.btn_close.clicked.connect(self.close)

    # ---------------- 打击垫 ----------------

    def _on_pad_click(self, pitch: str):
        if not self.chk_insert.isChecked():
            return
        gap = float(self.cmb_gap.currentData() or 1.0)

        if self.cmb_write.currentData() == 'head' and self.model is not None:
            # 写进时间轴 —— 落在播放头（有选区就落选区起点）那一拍
            rng = self.tl_edit.selection_beats()
            at = rng[0] if rng else self.tl_edit.playhead

            # ★ 那一拍已经有音了？那就**叠上去变成和弦**（同时发声），
            #   不占新的时间 —— 这就是「一个时间里放好几个音」的入口。
            target = self.model.note_starting_at(at, tol=CHORD_TOL)
            if (target is not None and pitch not in target.pitches):
                self._push_undo()
                self.model.add_pitch(target, pitch)
                self._sync_model(target.start)
                self.lbl_pos.setText(
                    '叠成和弦 <b>%s</b>（第 %.2f 拍，一起响）'
                    % (target.label, target.start))
                return

            self._push_undo()
            toks = tokens_for(pitch, gap)
            idx = self.model.rest_index_after_beat(at)
            self.model.insert_tokens(idx, toks)
            total = sum(parser.token_duration(t)[0] for t in toks)
            self._sync_model(at + total)
            return

        # 写进谱面文本的光标处
        self._insert(' '.join(tokens_for(pitch, gap)) + ' ')

    def _sync_model(self, head: float):
        """把模型改动同步到文本 / 播放器 / 时间轴，播放头停在 head 拍。"""
        self._syncing = True
        self.text.setPlainText(self.model.rebuild())
        self._syncing = False
        self.player.set_model(self.model)
        self.tl_edit.set_model_keep_head(self.model, head)
        self._update_info(None)

    # ---------------- 撤销 ----------------

    def _push_undo(self):
        """改谱之前先存一份快照（Ctrl + Z 用）。"""
        if self._applying:
            return
        txt = self.text.toPlainText()
        if self._undo and self._undo[-1] == txt:
            return
        self._undo.append(txt)
        if len(self._undo) > UNDO_MAX:
            self._undo.pop(0)

    def _undo_once(self):
        if not self._undo:
            self.lbl_pos.setText('没有可撤销的步骤了')
            return
        txt = self._undo.pop()
        head = self.tl_edit.playhead
        self._applying = True
        self._syncing = True
        self.text.setPlainText(txt)
        self._syncing = False
        self._applying = False
        self._refresh_from_text(keep_head=head)
        self.lbl_pos.setText('已撤销上一步')

    def _insert(self, s: str):
        cur = self.text.textCursor()
        cur.insertText(s)
        self.text.setTextCursor(cur)

    # ---------------- 文本 <-> 模型 ----------------

    def _on_text_changed(self):
        if self._syncing:
            return
        self._debounce.start()

    def _refresh_from_text(self, keep_head: float | None = None):
        sheet = parser.parse(self.text.toPlainText())
        self.model = EditModel(sheet)
        if keep_head is None:
            self.tl_edit.set_model(self.model)
        else:
            self.tl_edit.set_model_keep_head(self.model, keep_head)
        self.player.set_model(self.model)
        self.player.bpm = self._bpm(sheet)
        self._update_info(sheet)

    def _on_timeline_changed(self):
        if self.model is None:
            return
        self._syncing = True
        self.text.setPlainText(self.model.rebuild())
        self._syncing = False
        self.player.set_model(self.model)
        self._update_info(None)

    def _update_info(self, sheet=None):
        if self.model is None:
            self.info.setText('')
            return
        parts = [self.model.stats()]
        if sheet is not None:
            tl = timeline.Timeline(sheet)
            parts.append('约 %.1f 秒' % tl.total_sec)
            bad = layout.unmapped_pitches(tl.all_pitches())
            if bad:
                parts.append('<span style="color:#ff9a9a">琴上没有：%s</span>'
                             % ' '.join(bad))
        self.info.setText('　·　'.join(parts))

    def _bpm(self, sheet=None) -> int:
        if sheet is None:
            sheet = parser.parse(self.text.toPlainText())
        for ev in sheet.events:
            if isinstance(ev, parser.BpmChange):
                return int(ev.bpm)
        return 120

    # ---------------- 试听 ----------------

    def _play_from_beat(self, beat: float):
        if self.model is None:
            return
        self.player.bpm = self._bpm()
        self.tl_edit.set_playhead(beat)
        self.player.play_from(beat)

    # ---------------- 区域操作 ----------------

    def _play_selection(self):
        """从选区起点播到选区终点。"""
        rng = self.tl_edit.selection_beats()
        if not rng:
            self.lbl_pos.setText('还没有选区 —— 在时间轴空白处拖一下就框选了')
            return
        self.player.bpm = self._bpm()
        self.tl_edit.set_playhead(rng[0])
        self.player.play_from(rng[0], stop_at=rng[1])
        self.lbl_pos.setText('播放选区：第 %.2f ~ %.2f 拍' % rng)

    def _delete_selection(self):
        rng = self.tl_edit.selection_beats()
        if not rng:
            self.lbl_pos.setText('没有选区可删 —— 在时间轴空白处拖一下框选')
            return
        n = self.tl_edit.delete_selection()
        self.lbl_pos.setText('已删除选区内的 %d 个块（后面的已往前接上）' % n)

    def _clear_all(self):
        if not (self.model and self.model.notes):
            return
        r = QMessageBox.question(
            self, '全删', '确定要把整份谱面清空吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        self._push_undo()
        self.model.clear_all()
        self._syncing = True
        self.text.setPlainText('')
        self._syncing = False
        self.player.set_model(self.model)
        self.tl_edit.set_model(self.model)
        self._update_info(None)
        self.lbl_pos.setText('已清空')

    def _update_sel_label(self):
        rng = self.tl_edit.selection_beats()
        if not rng:
            self.lbl_sel.setText('没有选区（在时间轴空白处拖一下就能框选）')
        else:
            self.lbl_sel.setText(
                '已选 %.2f ~ %.2f 拍（共 %.2f 拍，%d 个块）'
                % (rng[0], rng[1], rng[1] - rng[0],
                   len(self.model.notes_in_range(*rng)) if self.model else 0))

    def _on_player_tick(self, beat: float):
        self.tl_edit.set_playhead(beat)
        self._on_playhead(beat)

    def _on_player_done(self):
        self.lbl_pos.setText('位置：播放完毕')

    def _on_playhead(self, beat: float):
        total = self.model.total_beats if self.model else 0.0
        self.lbl_pos.setText('位置：第 %.2f 拍 / 共 %.2f 拍' % (beat, total))

    def _on_playhead_dropped(self, beat: float):
        """拖红线松手 —— 正在播的话就从新位置接着播。"""
        if self.player.playing:
            self._play_from_beat(beat)

    # ---------------- 文件 ----------------

    def load_file(self, path: str):
        self.path = path
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            for enc in ('utf-8-sig', 'utf-8', 'gbk'):
                try:
                    self.text.setPlainText(raw.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
        except Exception as e:
            QMessageBox.warning(self, '读取失败', str(e))

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        try:
            parser.save(self.path, self.text.toPlainText())
            self.saved = True
            self.setWindowTitle('打谱器 — %s' % os.path.basename(self.path))
            return True
        except Exception as e:
            QMessageBox.warning(self, '保存失败', str(e))
            return False

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, '另存为', self.path or '新谱面.txt',
            '谱面文件 (*.txt);;所有文件 (*)')
        if not path:
            return False
        self.path = path
        return self.save()

    def closeEvent(self, event):
        self.player.stop()
        super().closeEvent(event)
