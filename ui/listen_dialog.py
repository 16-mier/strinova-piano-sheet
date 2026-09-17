# -*- coding: utf-8 -*-
"""听音记谱 —— 录一段在游戏里弹的琴声，自动转成谱面。

用法很简单：
    1. 选好要录哪个输出设备（游戏声音从哪儿出来就选哪个）
    2. 点「开始录音」（或者按全局热键），在游戏里把那首曲子弹一遍
    3. 点「停止并识别」，过几秒就能看到还原出来的谱子
    4. 满意就点「插到编辑器里」接着改
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from core import recorder, transcribe, layout


class ListenDialog(QDialog):
    """听音记谱对话框（非模态 —— 这样全局热键还能用）。"""

    def __init__(self, parent=None, bpm: int = 120,
                 device_keyword: str = '', insert_cb=None):
        super().__init__(parent)
        self.setWindowTitle('听音记谱 —— 录下来自动变谱子')
        self.resize(880, 640)
        self.setModal(False)

        self.rec = recorder.LoopbackRecorder()
        self.bpm = int(bpm)
        self.insert_cb = insert_cb
        self._text = ''
        self._hits: list = []
        self._device_keyword = device_keyword

        # ---------------- 顶部：设备 / BPM ----------------
        g_set = QGroupBox('录音设置')
        top = QHBoxLayout(g_set)

        self.cmb_dev = QComboBox()
        self.cmb_dev.setMinimumWidth(320)
        btn_refresh = QPushButton('刷新设备')
        btn_refresh.clicked.connect(self._fill_devices)

        self.sp_bpm = QSpinBox()
        self.sp_bpm.setRange(30, 300)
        self.sp_bpm.setValue(self.bpm)
        self.sp_bpm.setSuffix(' BPM')

        top.addWidget(QLabel('录音设备（游戏声音从哪出来就选哪个）'))
        top.addWidget(self.cmb_dev, 1)
        top.addWidget(btn_refresh)
        top.addSpacing(12)
        top.addWidget(QLabel('曲速'))
        top.addWidget(self.sp_bpm)

        # ---------------- 中部：录音控制 ----------------
        g_rec = QGroupBox('录音')
        mid = QVBoxLayout(g_rec)

        row = QHBoxLayout()
        self.btn_rec = QPushButton('●  开始录音')
        self.btn_rec.setFixedHeight(38)
        self.btn_rec.setStyleSheet(
            'QPushButton{font-weight:bold;font-size:14px;}')
        self.btn_stop = QPushButton('■  停止并识别')
        self.btn_stop.setFixedHeight(38)
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_rec, 1)
        row.addWidget(self.btn_stop, 1)
        mid.addLayout(row)

        self.lbl_state = QLabel('待命。可以按全局热键「听音记谱」开始/结束。')
        self.lbl_state.setStyleSheet('color:#9aa3b8;')
        mid.addWidget(self.lbl_state)

        self.lbl_result = QLabel('')
        self.lbl_result.setWordWrap(True)
        mid.addWidget(self.lbl_result)

        # ---------------- 下部：识别结果 ----------------
        g_out = QGroupBox('识别结果（可以直接改，再插进编辑器）')
        bottom = QVBoxLayout(g_out)
        self.text = QPlainTextEdit()
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSizeF(12)
        self.text.setFont(mono)
        self.text.setPlaceholderText('识别完会显示在这里…')
        bottom.addWidget(self.text, 1)

        row2 = QHBoxLayout()
        self.chk_keep = QCheckBox('保留识别结果，不覆盖我改过的内容')
        self.btn_apply = QPushButton('插到编辑器里')
        self.btn_apply.setFixedHeight(32)
        self.btn_copy = QPushButton('复制')
        self.btn_copy.setFixedHeight(32)
        self.btn_apply.setStyleSheet('QPushButton{font-weight:bold;}')
        row2.addWidget(self.chk_keep, 1)
        row2.addWidget(self.btn_copy)
        row2.addWidget(self.btn_apply)
        bottom.addLayout(row2)

        root = QVBoxLayout(self)
        root.addWidget(g_set)
        root.addWidget(g_rec)
        root.addWidget(g_out, 1)

        # ---------------- 接线 ----------------
        self.btn_rec.clicked.connect(self.toggle_record)
        self.btn_stop.clicked.connect(self.stop_and_transcribe)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_copy.clicked.connect(
            lambda: self.text.selectAll() or self.text.copy())

        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._update_state)

        self._fill_devices()

    # ---------------- 设备 ----------------

    def _fill_devices(self):
        self.cmb_dev.clear()
        for name, dev_id in recorder.list_output_devices():
            self.cmb_dev.addItem(name, dev_id)
        # 尽量选到上次那个
        if self._device_keyword:
            want = recorder.find_device_id(self._device_keyword)
            if want is not None:
                for i in range(self.cmb_dev.count()):
                    if self.cmb_dev.itemData(i) == want:
                        self.cmb_dev.setCurrentIndex(i)
                        break

    # ---------------- 录音 ----------------

    def toggle_record(self):
        if self.rec.recording:
            self.stop_and_transcribe()
        else:
            self.start_record()

    def start_record(self):
        dev_id = self.cmb_dev.currentData()
        if not dev_id:
            QMessageBox.warning(self, '没有录音设备',
                                '没列到可用的输出设备，点「刷新设备」试试。')
            return
        self.bpm = self.sp_bpm.value()
        ok = self.rec.start(str(dev_id), channels=2)
        if not ok:
            self.rec.abort()
            ok = self.rec.start(str(dev_id), channels=1)
        if not ok:
            QMessageBox.warning(
                self, '开不了录音流',
                '这个设备录不了：\n%s\n\n'
                '换个设备试试，或者先把游戏声音换成别的输出。'
                % (self.rec.last_error or '(没有更多信息)'))
            return
        self.btn_rec.setText('●  录音中…（再按一下结束）')
        self.btn_stop.setEnabled(True)
        self.lbl_state.setText('正在录…去游戏里弹吧。')
        self._tick.start()

    def stop_and_transcribe(self):
        data = self.rec.stop()
        self._tick.stop()
        self.btn_rec.setText('●  开始录音')
        self.btn_stop.setEnabled(False)

        if len(data) < 4800:
            self.lbl_state.setText('录到的东西太短（%.1f 秒），没东西可分析。'
                                   % (len(data) / max(1, self.rec.samplerate)))
            return

        self.lbl_state.setText('录到 %.1f 秒，正在识别…' % (
            len(data) / max(1, self.rec.samplerate)))
        QTimer.singleShot(30, lambda: self._do_transcribe(data))

    def _do_transcribe(self, data):
        rate = self.rec.samplerate
        tokens, hits = transcribe.transcribe(data, rate, bpm=self.bpm)

        if not hits:
            self.lbl_result.setText(
                '<span style="color:#ff9a9a">没识别出音符。'
                '可能录到的是静音、或者选的设备不对。</span>')
            return

        scale = [h for h in hits if abs(h.cents) <= 35]
        self._hits = hits
        self.lbl_result.setText(
            '识别到 <b>%d</b> 个音，其中 <b>%d</b> 个音高对得很准（±35 音分内）。'
            '共 %.1f 秒、曲速 %d BPM。'
            % (len(hits), len(scale), len(data) / rate, self.bpm))

        if not self.chk_keep.isChecked():
            text, _ = transcribe.to_sheet_text(
                data, rate, bpm=self.bpm, title='听音记谱')
            self._text = text
            self.text.setPlainText(text)

        self.lbl_state.setText('识别完了。检查一下结果，满意就插到编辑器里。')

    def _update_state(self):
        if self.rec.recording:
            self.lbl_state.setText('正在录… %.1f 秒（去游戏里弹吧）'
                                   % self.rec.seconds)

    # ---------------- 输出 ----------------

    def apply_text(self, text: str) -> bool:
        """把一段谱面文本塞进来（供热键流程或外部调用）。"""
        self.text.setPlainText(text)
        self._text = text
        return True

    def _apply(self):
        text = self.text.toPlainText().strip()
        if not text:
            self.lbl_state.setText('还没有内容可以插。')
            return
        if self.insert_cb is not None:
            self.insert_cb(text)
            self.lbl_state.setText('已经插进编辑器了。')
        else:
            self.lbl_state.setText('没有接上编辑器，请手动复制。')

    # ---------------- 收尾 ----------------

    def closeEvent(self, event):
        self._tick.stop()
        self.rec.abort()
        super().closeEvent(event)
