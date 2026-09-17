# -*- coding: utf-8 -*-
"""谱面编辑器 —— 左边写谱、右边实时看解析结果。"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core import layout, parser, timeline

HELP = """记谱速查
音高  1~7 中音 ｜ #4 半音 ｜ 6. 低音 ｜ 3' 高音 ｜ 1'' 倍高音
节奏  基础 1 拍；每个 - 加 1 拍；每个 ^ 乘 0.5
      1-   = 2 拍      ^1   = 0.5 拍
      ^^1  = 0.25 拍   ^1-- = 1.5 拍（附点四分）
      ^^1-- = 0.75 拍（附点八分）
休止  单独写 - 是休止 1 拍；^- 是休止 0.5 拍
和弦  用 & 连起来：1'&3'&5'    也能带节奏：^1&3&5
变速  #120# 写在任意位置，实时改 BPM
排版  空格换行随意；不像乐谱的文字会被跳过，且不留空档
"""

SAMPLE = """小星星
#100#
1 1 5 5 6 6 5-
4 4 3 3 2 2 1-
"""


class EditorDialog(QDialog):
    """写谱 / 改谱。"""

    def __init__(self, path: str | None = None, parent=None):
        super().__init__(parent)
        self.path = path
        self.saved = False

        self.setWindowTitle('谱面编辑器')
        self.resize(980, 660)

        # ---- 左：文本 ----
        self.text = QPlainTextEdit()
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(12)
        self.text.setFont(mono)
        self.text.setTabChangesFocus(False)

        # ---- 快捷插入 ----
        quick = QHBoxLayout()
        for label, ins in [('^ 减半', '^'), ('- 加一拍', '-'),
                           ('& 和弦', '&'), ("' 高八度", "'"),
                           ('# 半音', '#'), ('#120# 变速', '#120# '),
                           ('␣ 空格', ' '), ('↵ 换行', '\n')]:
            b = QPushButton(label)
            b.setFixedHeight(28)
            b.clicked.connect(lambda _=False, s=ins: self._insert(s))
            quick.addWidget(b)
        quick.addStretch(1)
        btn_help = QPushButton('速查表')
        btn_help.setFixedHeight(28)
        btn_help.clicked.connect(self._show_help)
        quick.addWidget(btn_help)

        # ---- 右：解析预览 ----
        self.info = QLabel('')
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.info.setMinimumWidth(300)
        self.info.setStyleSheet(
            'QLabel{background:#1b1f2a;color:#dfe4f0;padding:10px;'
            'border-radius:8px;font-size:12px;}')

        # ---- 底部按钮 ----
        self.btn_save = QPushButton('保存')
        self.btn_save_as = QPushButton('另存为…')
        self.btn_sample = QPushButton('插入示例')
        self.btn_close = QPushButton('关闭')
        self.btn_save.clicked.connect(self.save)
        self.btn_save_as.clicked.connect(self.save_as)
        self.btn_sample.clicked.connect(
            lambda: self.text.setPlainText(SAMPLE))
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_save, self.btn_save_as):
            b.setStyleSheet('QPushButton{font-weight:bold;padding:6px 16px;}')

        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_sample)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_save_as)
        bottom.addWidget(self.btn_close)

        left = QVBoxLayout()
        left.addLayout(quick)
        left.addWidget(self.text, 1)

        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(self.info, 2)

        root = QVBoxLayout(self)
        root.addLayout(body, 1)
        root.addLayout(bottom)

        # ---- 实时解析（防抖）----
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._refresh)
        self.text.textChanged.connect(self._debounce.start)

        if path and os.path.isfile(path):
            self.load_file(path)
        else:
            self.text.setPlainText(SAMPLE)
        self._refresh()

    # ---- 操作 ----

    def _insert(self, s: str):
        cur = self.text.textCursor()
        cur.insertText(s)
        self.text.setTextCursor(cur)

    def _show_help(self):
        QMessageBox.information(self, '记谱速查', HELP)

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
            self.setWindowTitle('谱面编辑器 — %s'
                                % os.path.basename(self.path))
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

    # ---- 预览 ----

    def _refresh(self):
        sheet = parser.parse(self.text.toPlainText())
        if not sheet:
            self.info.setText('还没有有效的音符。\n\n' + HELP)
            return

        tl = timeline.Timeline(sheet)
        bad = layout.unmapped_pitches(tl.all_pitches())
        rows = [
            '<b>%s</b>' % (sheet.title or '(未命名)'),
            '',
            '音符数：<b>%d</b>' % len(sheet),
            '总拍数：%.1f' % tl.total_beats,
            '时长：约 <b>%.1f 秒</b>' % tl.total_sec,
            'BPM：%d%s' % (tl.bpm_points[0][1] if tl.bpm_points else 120,
                           '（含变速）' if len(tl.bpm_points) > 1 else ''),
        ]
        if bad:
            rows += ['',
                     '<span style="color:#ff9a9a">琴上没有的音（会被跳过）：'
                     '<br>%s</span>' % ' '.join(bad)]
        rows += ['', '<b>开头预览</b>']
        for it in tl.items[:26]:
            if it.chord.is_rest:
                name = '休止'
            else:
                name = '+'.join(it.chord.pitches)
            rows.append('%6.2fs  %-12s %s 拍'
                        % (it.start_sec, name, _fmt(it.chord.duration)))
        if len(tl) > 26:
            rows.append('…')
        self.info.setText('<br>'.join(rows))


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return '%g' % x
