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

        tl_box = QGroupBox('时间轴（拖动音符改间距 · Ctrl+滚轮缩放 · '
                           '双击音符从这里播）')
        tlb = QVBoxLayout(tl_box)
        tlb.addWidget(self.tl_scroll, 1)

        self.btn_play = QPushButton('▶  从这里播')
        self.btn_play.setFixedHeight(30)
        self.btn_stop = QPushButton('⏹  停止')
        self.btn_stop.setFixedHeight(30)
        self.btn_home = QPushButton('⏮  回到开头')
        self.btn_home.setFixedHeight(30)
        self.lbl_pos = QLabel('位置：第 0 拍')
        self.lbl_pos.setStyleSheet('color:#9aa3b8;')
        rowp = QHBoxLayout()
        rowp.addWidget(self.btn_play)
        rowp.addWidget(self.btn_stop)
        rowp.addWidget(self.btn_home)
        rowp.addSpacing(16)
        rowp.addWidget(self.lbl_pos, 1)
        tlb.addLayout(rowp)

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
        self.tl_edit.seek_requested.connect(self._play_from_beat)

        self.btn_play.clicked.connect(
            lambda: self._play_from_beat(self.tl_edit.playhead))
        self.btn_stop.clicked.connect(self.player.stop)
        self.btn_home.clicked.connect(
            lambda: (self.tl_edit.set_playhead(0.0), self._on_playhead(0.0)))
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
        toks = tokens_for(pitch, gap)
        self._insert(' '.join(toks) + ' ')

    def _insert(self, s: str):
        cur = self.text.textCursor()
        cur.insertText(s)
        self.text.setTextCursor(cur)

    # ---------------- 文本 <-> 模型 ----------------

    def _on_text_changed(self):
        if self._syncing:
            return
        self._debounce.start()

    def _refresh_from_text(self):
        sheet = parser.parse(self.text.toPlainText())
        self.model = EditModel(sheet)
        self.tl_edit.set_model(self.model)
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

    def _on_player_tick(self, beat: float):
        self.tl_edit.set_playhead(beat)
        self._on_playhead(beat)

    def _on_player_done(self):
        self.lbl_pos.setText('位置：播放完毕')

    def _on_playhead(self, beat: float):
        total = self.model.total_beats if self.model else 0.0
        self.lbl_pos.setText('位置：第 %.2f 拍 / 共 %.2f 拍' % (beat, total))

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
