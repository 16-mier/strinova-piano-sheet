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
import re

from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QIcon, QSyntaxHighlighter,
                         QTextCharFormat)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QTextEdit, QVBoxLayout,
)

from core import encode, layout, parser, timeline
from core.edit_model import SPB, EditModel, pitch_order

from . import appstyle, theme as T
from .edit_player import EditPlayer
from .keypad import KeyPad
from .timeline_edit import (LANES, LANES_MAX, ROW_H, RULER_H,
                            TimelineEditor)

# ★ 示例谱面也用新格式 ★（老写法虽然还认，但示例没必要教旧的）
SAMPLE = """小星星
0:1 0.5:1 1:5 1.5:5 2:6 2.5:6 3:5
3.5:4 4:4 4.5:3 5:3 5.5:2 6:2 6.5:1
"""

# 写进文本时一个音默认占多长（拍）。1 拍 = 0.5 秒。
#
# ★ 这里原来是个 `GAP_CHOICES` 下拉框（界面上叫"触发间距"）★
#   用户：「这个触发间距没必要保留」。
#   它只影响"写入位置 = 谱面文本光标"那条路（生成 `1-` `^1` 的老写法）；
#   默认那条路（时间轴）每个音都是绝对时间，间距取决于你拖到哪儿。
GAP_DEFAULT = 1.0

CHORD_TOL = 0.2         # 播放头落在音起点 ±这个量内 = 叠成和弦（内部单位：拍，≈0.1 秒）
UNDO_MAX = 60           # 撤销栈存多少步

# 时间轴谱面的一行：`秒:音高`。
#   音高可以带 `'`（高八度）、`#`（半音）、`.`（低八度）、`&`（和弦），
#   冒号后面那只管抓到空白为止（换行会被 highlightBlock 切开）。
_TOKEN_RE = re.compile(r'(\d+(?:\.\d+)?)\s*:\s*([^\s]+)')


class SheetHighlighter(QSyntaxHighlighter):
    """谱面文本框的语法高亮：时间灰、音高按音区上色。

    ★ 颜色跟时间轴是**同一套** ★
      音高取 `theme.ZONE_COLORS` —— 就是时间轴上方块用的那几个色。
      于是"文本里这个 5"和"时间轴上那个方块"一眼能对上号，
      看谱面的时候不用在两个视图之间重新建立映射。

    ★ 不认识的行原样留着 ★
      标题、`#120#` 这类标记、还有随手写的注释都不着色（用默认前景色），
      不会因为解析不了就走样。
    """

    def __init__(self, doc):
        # ★ PyQt6 的构造签名只收一个参数 ★
        #   C++ 那边是 `QSyntaxHighlighter(QTextDocument *parent)`；
        #   按"文档 + 父对象"两个参数写会直接 `TypeError:
        #   arguments did not match any overloaded call`。
        super().__init__(doc)
        self._cache: dict[str, QTextCharFormat] = {}
        self._time = QTextCharFormat()
        self._time.setForeground(QColor(T.TEXT_DIM))

    def _pitch_format(self, pitch: str) -> QTextCharFormat:
        # 缓存一下：`highlightBlock` 会被反复调用，每次新建 QTextCharFormat
        # 在大谱面上是笔实打实的开销。
        hit = self._cache.get(pitch)
        if hit is not None:
            return hit
        fmt = QTextCharFormat()
        cell = layout.pitch_to_cell(pitch)
        if cell is None:
            fmt.setForeground(QColor(T.TEXT))
        else:
            fmt.setForeground(QColor(T.ZONE_COLORS[T.zone_of(cell[0])]))
            fmt.setFontWeight(QFont.Weight.DemiBold)
        self._cache[pitch] = fmt
        return fmt

    def highlightBlock(self, text: str):
        for m in _TOKEN_RE.finditer(text):
            self.setFormat(m.start(1), m.end(1) - m.start(1), self._time)
            body = m.group(2)
            base = m.start(2)
            # 和弦里每个音各自取色（`1&3&5` 可能横跨几个音区）
            pos = 0
            for part in body.split('&'):
                if part:
                    self.setFormat(base + pos, len(part),
                                   self._pitch_format(part))
                pos += len(part) + 1


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


def tokens_for_chord(pitches: list[str], gap: float) -> list[str]:
    """和弦版 —— 把一串音高用 `&` 连成一个 token，再走单音那套。

    ★ 现在没有生产代码在调它了 ★ —— 用户：「和弦功能用不到」，
      打击垫那套多选（拖动划过 / Shift 框选 / Shift 逐个挑 + 写和弦
      按钮）整套删掉之后，就没有"一次录入好几个音"的入口了。

      留着是因为 `tests/test_core.py` 和 `tools/` 里那几个诊断脚本
      还在用它构造和弦样本 —— 删了它们就跑不起来。
      谱面里**读**和弦这条路（`parser` 的 `&`）本来就没动过。
    """
    uniq = sorted({p for p in pitches if p}, key=pitch_order)
    if not uniq:
        return []
    return tokens_for('&'.join(uniq), gap)


class _SecNote:
    """把 `EdNote` 的「拍」换算成「秒」，再交给 `timeline_from_notes()`。

    ★ 为什么需要这一层 ★

      `EdNote.start` 的单位是**拍**（`core/edit_model.py` 里
      `start = 秒 / SPB`，字段注释也写着"起始拍"），
      而 `Timeline` 要的 `Chord.at` 是**绝对秒**。

      `timeline_from_notes()` 只是把 `.start` 原样当成秒用 ——
      **它本身没错**，那是它的契约。错的是把**拍值**递了进去。

    ★ 不这么做的后果（实测）★

      浮窗那条时间轴整体被放大一倍：
      `demo.txt` 在主界面是 **28.8 秒**，经制谱器这条路变成 **55.2 秒**。
      而喂给浮窗的播放位置是**真实秒** —— 于是浮窗只走到
      "已播放时长的一半"，弹得越久落后越多。

      用户报的「**都下俩个按键了显示还是上俩个**」就是这个：

          真实 2.1~2.7 秒时，该弹第 4~5 个音
          浮窗深色格却停在第 2~3 个 —— 恰好落后两个

    ★ 为什么换算放在这里、不放 `core/timeline.py` ★

      那边**故意**不 import `SPB`（见它自己的 docstring：
      "只用到 `.start` / `.pitches` / `.is_rest` / `.label` 四个属性，
      所以这里不用导入 `edit_model`，也就不会有循环依赖"）。
      所以由**交出数据的一方**负责把单位换算好。
    """

    __slots__ = ('start', 'pitches', 'is_rest', 'label')

    def __init__(self, n, spb: float):
        self.start = float(getattr(n, 'start', 0.0)) * float(spb)
        self.pitches = list(getattr(n, 'pitches', []) or [])
        self.is_rest = bool(getattr(n, 'is_rest', False))
        self.label = getattr(n, 'label', '') or '+'.join(self.pitches)


class EditorDialog(QDialog):
    """制谱器。

    ★ 对外只发一个信号：`timeline_tick(秒)` ★
      谱面浮窗靠它跟着制谱器走 —— 用户：「在制谱器播放的时候这个
      窗口要跟着制谱器来」「按键显示跟随」。制谱器不该知道浮窗
      长什么样，只管把"我现在到第几秒了"报出去，由控制台去接。
    """

    timeline_tick = pyqtSignal(float)      # 当前播放位置（秒）
    # ★ 打击垫被点了一下：音高列表（单音也是一个元素的列表）★
    #
    #   用户：「你直接让这个的逻辑跟着**打击垫**走就行」。
    #
    #   打击垫是**同步事件** —— 你点了哪个键，程序当场就知道，
    #   不用听、不用认、不会被 BGM 骗。所以浮窗跟着它走就是
    #   零延迟、零误报、零猜测。
    #
    #   为什么非要单开一个信号：`timeline_tick` 报的是**播放头**
    #   到哪儿了，而点打击垫**不改播放头**。所以以前在制谱器里
    #   连点 5 下，浮窗一直停在原地 —— 看着就跟"落后好几个"一样。
    pad_hit = pyqtSignal(list)

    def __init__(self, path: str | None = None, parent=None):
        super().__init__(parent)
        # ★ 主题放在最前面 ★ —— `setStyle('Fusion')` 只在控件建出来**之前**
        #   生效，晚一步就会出现"半新半旧"。幂等，重复调用无害。
        #   （放在这里而不是只放 `main.py`，是为了让工具脚本
        #     `tools/test_editor_chord.py` 这类直接构造对话框的入口
        #     也能拿到同一套外观。）
        appstyle.apply(QApplication.instance())

        # ★ 让它能最小化到任务栏 ★
        #   用户：「控制台和琴谱器开启的时候俩个都能任意一个挂载后台」——
        #   两个都开着的时候，想先把哪一个收起来都行，回头再点回来。
        #
        #   ★ QDialog 默认**没有**最小化按钮 ★
        #     Qt 给对话框的默认窗口标志是
        #     `Dialog | WindowTitleHint | WindowCloseButtonHint` ——
        #     标题栏上只有关闭。所以显式补一个
        #     `WindowMinimizeButtonHint`。
        #     （控制台那边不用动：它是 `QMainWindow`，本来就带。）
        #
        #   ★ 必须在这儿设，不能等显示之后再设 ★
        #     窗口一旦 show 出来，`setWindowFlags()` 会把它**藏起来**
        #     （Qt 的已知行为：改标志要重建原生窗口）。
        #     构造期间设没有这个副作用 —— 那会儿还没 show。
        #
        #   ★ 它是非模态的，最小化不会冻住控制台 ★
        #     `control.py` 里特意用 `setModal(False)` + `show()`
        #     （见那段注释：模态会把控制台整个冻住）。
        #     所以收起哪一个都不影响另一个照常干活。
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint)

        self.path = path
        self.saved = False
        self.model: EditModel | None = None
        # 给浮窗用的 Timeline 缓存（模型一变就作废）
        self._tl_cache = None
        self._tl_dirty = True
        self._syncing = False
        self._undo: list[str] = []
        self._applying = False          # 正在回滚，别再记一笔
        self._rec_armed = False         # 「节拍记录」预备中：等第一个音才开始计时
        self._rec_from = 0.0            # 本次记录的起点（= 点「⏺」时红线在哪儿）
        # ★ 时钟"该不该走" ★
        #   以前这里是一个 `_clock_booted`（只自动启动一次）+ 一个
        #   `_clock_user_stopped`（用户停过就别拉起来），再加上 showEvent
        #   里的自动启动 —— 三者叠在一起，结果是**一打开编辑器时间轴就自己
        #   静音跑起来**，用户看到的就是「点开谱面会自动播放而且没有声音」。
        #   现在只有一个标志：**你按下空格 / ▶ / 开始记录，它才该走**。
        #   看门狗只负责"该走的时候别停"，绝不负责"启动"。
        self._clock_should_run = False

        self.setWindowTitle('打谱器')
        self.resize(1240, 840)

        # ================= 左上：打击垫 =================
        self.pad = KeyPad()
        # ★ 不再写死 300，但下限也别给太高 ★
        #   用户要把打击垫做大，可它得是"能缩"的 —— 写死会让外圈空间
        #   整个浪费；下限给太高（试过 280）则会在这一列装不下时**溢出**：
        #   实测打击垫的格子会跟下面的按钮、复选框叠在一起。
        #   给个能缩的下限，剩下的交给布局。
        self.pad.setMinimumSize(200, 200)
        n = self.pad.load_notes()

        pad_box = QGroupBox('打击垫（点了就出声）')
        pb = QVBoxLayout(pad_box)
        # ★ `stretch=1`，而且**不带对齐** ★
        #   带 `AlignHCenter` 的话 Qt 只按 `sizeHint` 摆它、**绝不拉伸** ——
        #   打击垫就永远卡在 `minimumSize` 那个尺寸上，"做大点"根本没发生。
        #   去掉对齐、给上 stretch，它会填满这一列的可用高度；
        #   而 `KeyPad._cell_rect()` 内部是按 `min(宽, 高)` 居中的，
        #   所以不管被拉成什么形状，格子都还是正方形。
        pb.addWidget(self.pad, 1)

        # ★ 「手动演奏」★ —— 挨着打击垫放
        #   用户：「这个改成手动演奏，点一下开启再点一下关闭，然后放在
        #   打击垫旁边，明显一点的样式」。
        #   它跟打击垫本来就是一件事（你弹、它记），放一起之后
        #   "开启 → 弹 → 再点一下关"是一条线，不用再去工具栏里找。
        #   ★ 关键在"第一个音就是起点" ★
        #     先点开启、再跑过去按第一个音，中间那段时间差不固定，
        #     录出来整段就飘了。所以开启之后先**等**：你按下的第一个音
        #     就是起点，时间轴从那一刻才开始走。
        self.btn_rec = QPushButton('手动演奏')
        # ★ 图标自己画，不是 🎹 ★
        #   它跟浮窗控制条上那几颗是同一类字符：真机上会被系统换成
        #   **彩色 emoji**，压在深色按钮上跟旁边自己画的图标不是一套。
        self.btn_rec.setIcon(QIcon(appstyle.record_icon(15)))
        self.btn_rec.setIconSize(QSize(15, 15))
        self.btn_rec.setObjectName('record')
        self.btn_rec.setCheckable(True)       # 开关样：开着的时候整块变红
        # ★ 高度：它现在是**独立一行的大按钮** ★（放到 `root` 里去了，
        #   见那边的说明）。用户：「这里按钮太小了，需要大的按钮，
        #   直接单开一个地方放」。
        self.btn_rec.setMinimumHeight(58)
        self.btn_rec.setToolTip(
            '点一下开启 —— 之后你按的每个键都按真实时间记进谱面；\n'
            '再点一下关闭。\n\n'
            '开启后先等你按第一个音：它就是起点，时间轴从那一刻才开始走，\n'
            '所以不用抢时间。起点跟着红线走 —— 想接着往下录，\n'
            '先把红线拖到那儿。')
        # （按钮本身**不放在这一列**里 —— 见 `root.addWidget(self.btn_rec)`。
        #   塞在打击垫底下时它跟复选框、说明文字挤成一列，
        #   第一眼看不出那是个"开关"。）

        # ★ 「触发间距」下拉框删掉了 ★
        #   用户：「这个触发间距没必要保留」。
        #   它只影响**写进文本**那条路（"写入位置 = 谱面文本光标"时
        #   生成 `1-` `^1` 这种老写法）；而默认那条路（时间轴）每个音
        #   都是绝对时间，间距取决于你把它拖到哪儿，跟这个框毫无关系。
        #   留个下拉框在那儿，反而让人以为它能控制播放节奏。
        #   现在固定用 `GAP_DEFAULT`。
        self.chk_insert = QCheckBox('点击时同步写入谱面')
        self.chk_insert.setChecked(True)
        self.chk_insert.setToolTip(
            '关掉的话打击垫只出声、不写谱（当试听键用）\n'
            '注意：拖动多选 / Shift 框选出来的一串键，\n'
            '勾上时会被写成一个和弦（同一时刻一起响）')
        gap_row = QHBoxLayout()
        gap_row.addWidget(self.chk_insert)
        gap_row.addStretch(1)
        pb.addLayout(gap_row)

        # ★ 拖动行为：默认全部"自由"，跟剪辑软件的时间轴一样 ★
        #   原来这两个行为都是**硬编码**的，而且都是"会自动变整齐"：
        #   位置吸附到 1/4 拍网格、拖到前一个音附近自动并成和弦。
        #   用户的原话是「不是拖动向左就直接和上一个整齐的，
        #   而是跟制作视频的时间轴一样」。
        self.chk_snap = QCheckBox('拖动吸附 0.05 秒')
        self.chk_snap.setToolTip(
            '不勾（默认）：拖到哪儿就是哪儿。\n'
            '勾上：位置吸附到 0.05 秒一档，方便对齐。')
        self.chk_auto_lane = QCheckBox('重叠的自动错开轨道')
        self.chk_auto_lane.setChecked(True)
        self.chk_auto_lane.setToolTip(
            '勾上（默认）：时间上叠住的方块自动落到别的轨道上。\n'
            '手动拖过轨道的方块不会被自动挪走。')
        drag_row = QHBoxLayout()
        drag_row.addWidget(self.chk_snap)
        drag_row.addWidget(self.chk_auto_lane)
        # ★ 「点轨道音符听音」★
        #   用户：「新增一个选项，打勾的，点一下可以预览，预览的是在这个
        #   界面里点一下音符直接发出这个音符的声音」。
        #   时间轴那个 `note_clicked` 信号一直都有（点块就发），
        #   只是**从来没人接** —— 接上打击垫的音源就成了"点一下听个响"。
        #   放在这一行是为了**不占新的高度**：这一列已经很挤了。
        #   ★ 名字改过一次 ★ —— 用户：「这个应该改成点轨道音符听音」：
        #     原来叫「点音符试听」，光看字不知道是点哪儿的音符
        #     （打击垫上也是音符）。点名"轨道"才不会跟打击垫混。
        self.chk_preview = QCheckBox('点轨道音符听音')
        self.chk_preview.setToolTip(
            '勾上之后，在下面轨道上点一下音符方块就出声 ——\n'
            '改谱子的时候随手确认一下这个音对不对，不用按播放。')
        drag_row.addWidget(self.chk_preview)

        # ★ 「12 轨」开关 ★
        #   用户：「这里面新增一个按钮开启 12 轨，正常 6 轨」。
        #   时间轴原来是**固定 6 条**自由轨道（`timeline_edit.LANES`）——
        #   方块挤得厉害的时候就摊不开了，只能叠着。
        #   现在 6 是默认值，这颗按钮负责铺到 12 条。
        #
        #   ★ 为什么是按钮不是复选框 ★
        #     这一行已经三个复选框了，再来一个分不出来；
        #     而且它切换的是"整个时间轴的形状"，不是某个行为开关。
        #     做成可勾选的按钮，按下去是凹的、一眼看得出换了模式。
        #
        #   ★ 放在这一行是为了不占新高度 ★
        #     这一列已经很挤（见下面 `lbl_pad` 那段注释）。
        self.btn_lanes = QPushButton('12 轨')
        self.btn_lanes.setCheckable(True)
        self.btn_lanes.setObjectName('bar')     # 借控制条那颗小按钮的尺寸规则
        self.btn_lanes.setToolTip(
            '不按（默认）：时间轴 6 条轨道。\n'
            '按下去：铺 12 条 —— 方块时间上叠得厉害时能摊得更开。\n'
            '（切回 6 轨时，原来摆在第 7~12 轨上的方块会被收拢到第 6 轨。）')
        self.btn_lanes.toggled.connect(self._on_lanes_toggled)
        drag_row.addWidget(self.btn_lanes)
        drag_row.addStretch(1)
        pb.addLayout(drag_row)

        # ★ "音源已就绪"那行只在**失败**时才出现 ★
        #   成功了它没什么信息量，却占掉一行高度 —— 而这一列现在最缺的
        #   就是高度：打击垫要长大，挤不下就会跟下面的控件叠在一起
        #   （实测过：格子被"手动演奏"和复选框压住）。
        self.lbl_pad = QLabel('⚠ 音源载入失败 —— 打击垫不会出声')
        self.lbl_pad.setObjectName('dim')
        self.lbl_pad.setVisible(not n)
        pb.addWidget(self.lbl_pad)

        # ★ 那套"多选写和弦"删了 ★ —— 用户：「和弦功能用不到」
        #
        #   这里原来站着一个「⬒ 先在打击垫上 Shift + 点几个键」按钮
        #   （Shift 逐个挑完之后按它落笔），底下还有一行
        #   「拖动划过 = 一起选 · Shift = 逐个挑 / 框选一段」的说明。
        #
        #   那三种方式（按住拖动划过一串 / Shift 拖矩形 / Shift 逐个挑）
        #   服务的都是同一件事：同一时刻按下好几个键 = 谱面里一个
        #   `1&3&5` 的和弦块。用户不写和弦，整套一起删掉 ——
        #   打击垫现在只剩最直接的：点一下，出一个音，写一个音。
        hint = QLabel('点一下打击垫 = 出声 + 写进谱面')
        hint.setObjectName('mute')
        hint.setToolTip(
            '点一下键就出一个音，松手写进谱面。\n'
            '（要不要真写进去，看上边那个「同步写入谱面」）')
        pb.addWidget(hint)

        # ================= 右上：文本 =================
        self.text = QPlainTextEdit()
        # ★ 等宽字体走 `appstyle` ★
        #   顺带把字号提到 12.5 —— 时间轴格式是"一行好几个音"，
        #   位置数字竖向对齐才看得出版面，12 号在高分屏上偏挤。
        self.text.setFont(appstyle.mono_font(12.5))
        # ★ 给谱面文本上色 ★
        #   时间灰、音高按音区取色（跟时间轴上方块同一套）。
        #   高亮器挂在 document 上，得留个引用 —— 不然会被 GC 掉。
        self._hl = SheetHighlighter(self.text.document())

        # ★ 这排快捷按钮整排去掉了 ★
        #   用户：「制谱器这里面的也可以去掉」（指 `& ' # : 空格 速查`）。
        #   时间轴格式里位置直接写秒、音高直接写键名，靠键盘打就行，
        #   这排按钮除了占地方没别的用。
        #   （要点一下才能插入，本身还比打字慢；`#` / `:` 这些
        #     平时也不在中文输入法里，直接敲更方便。）
        text_box = QGroupBox('谱面文本')
        tb = QVBoxLayout(text_box)
        tb.addWidget(self.text, 1)

        # ★ 打击垫和谱面文本之间放一根**可拖的分隔条** ★
        #   用户：「这个 UI 可以优化可以大点，这个部分可以缩小，
        #   不一定用的到」—— 打击垫是他**弹**的地方，该给大；
        #   谱面文本多数时候只是"生成出来的结果"，
        #   真正在操作的是下面那条时间轴。
        #   两边都不写死：用 QSplitter，想怎么分自己拉，
        #   默认先给打击垫大一点。
        self.split_top = QSplitter(Qt.Orientation.Horizontal)
        self.split_top.addWidget(pad_box)
        self.split_top.addWidget(text_box)
        self.split_top.setSizes([460, 640])
        self.split_top.setStretchFactor(0, 0)
        self.split_top.setStretchFactor(1, 1)
        self.split_top.setChildrenCollapsible(False)
        self.split_top.setObjectName('top_split')

        # ================= 下面：时间轴 =================
        self.tl_edit = TimelineEditor()
        self.tl_scroll = QScrollArea()
        self.tl_scroll.setWidget(self.tl_edit)
        self.tl_scroll.setWidgetResizable(True)
        # ★ 高度按**轨道数**算，不写死 ★
        #   以前是 `16 * 24 + 60 = 444` —— 那是老版"16 行钢琴卷帘"
        #   留下来的数（每行 24px）。现在只有 6 条轨道、每行 40px，
        #   444 太高，底下白白空出一大块（实测空 200 多像素）。
        #   24 = 横向滚动条 + 上下边框的余量。
        self.tl_scroll.setMinimumHeight(RULER_H + LANES * ROW_H + 24)
        self.tl_scroll.setObjectName('timeline_scroll')

        tl_box = QGroupBox('时间轴')
        tlb = QVBoxLayout(tl_box)

        # ★ 操作说明从**标题**里搬出来，做成独立的一行说明 ★
        #   原来整段塞在 `QGroupBox` 的 title 里，有两个硬伤：
        #     · title 是**单行**的 —— 里面那三个 `\n` 之后的内容
        #       用户根本看不到（截图实测只显示第一行，
        #       "点一下选中" / "Shift 逐个多选" 这些全丢了）；
        #     · title 不解析 HTML（style 直接 `drawText`），
        #       `<b>` 会原样显示成一串标签。
        #   搬成 QLabel 之后两个问题一起消失，还能多行 + 关键词加粗。
        tl_hint = QLabel(
            '横轴 = <b>秒</b>　·　6 条自由轨道　·　方块等宽　·　'
            '拖方块：左右 = 改时间（<b>不带动后面的</b>），上下 = 换轨道<br>'
            '音高写在方块上（和弦写 <b>1&amp;3</b>）　·　点一下选中那一个，'
            'Delete 删它　·　右键拿掉一个音　·　按住红线拖播放头<br>'
            '空白处拖 = 框选（圈出要删的那一段）　·　Shift + 点方块 = '
            '逐个多选（青色，再点同一个取消）—— 挑中的能整组一起拖 / '
            'Delete 一起删 / Esc 清空')
        tl_hint.setWordWrap(True)
        tl_hint.setObjectName('dim')
        tlb.addWidget(tl_hint)

        tlb.addWidget(self.tl_scroll, 1)

        # ★ 三颗按钮的图标自己画 ★（原来写的是 ▶ / ⏹ / ⏮ 三个字符，
        #   真机上会被系统换成彩色 emoji —— 跟控制台那边是同一个坑）
        self.btn_play = QPushButton('从这里播')
        self.btn_play.setIcon(QIcon(appstyle.play_icon(13)))
        self.btn_play.setIconSize(QSize(13, 13))
        self.btn_play.setFixedHeight(30)
        self.btn_play.setToolTip('从播放头开始播　（空格键也能播 / 停）')
        self.btn_stop = QPushButton('停止')
        self.btn_stop.setIcon(QIcon(appstyle.stop_icon(13)))
        self.btn_stop.setIconSize(QSize(13, 13))
        self.btn_stop.setFixedHeight(30)
        self.btn_stop.setToolTip('停住，播放头留在原地 —— 再按空格从这儿接着播')
        self.btn_home = QPushButton('回到开头')
        self.btn_home.setIcon(QIcon(appstyle.back_icon(13)))
        self.btn_home.setIconSize(QSize(13, 13))
        self.btn_home.setFixedHeight(30)
        self.btn_home.setToolTip('播放头回到开头（0 秒）')

        # ★ 选区播放 = **循环** ★
        #   用户：「选区功能应该是在选区内循环播放」。
        #   一段反复听才是"选区"最有用的地方 —— 播一遍就停的话，
        #   想再听还得回去点播放头。
        # ★ 两个"删除选区" ★
        #   用户：「循环选区删掉，删除选区可以保留，选区内的方块全部删掉
        #   然后后面的方块可以选择向前靠齐或者留在原地，做俩个删除选区
        #   选项」。
        #   差别只在"后面的块跟不跟着挪"：
        #     · 靠齐（剪切）—— 删完立刻接上，做"这段不要了、后面顶上来"
        #     · 留空（挖掉）—— 后面的原地不动，那段变成空的
        #   两个都是常用操作、谁也当不了默认，所以并排放。
        self.btn_del_sel = QPushButton('删除并靠齐')
        self.btn_del_sel.setIcon(QIcon(appstyle.trash_icon(15)))
        self.btn_del_sel.setIconSize(QSize(15, 15))
        self.btn_del_sel.setToolTip(
            '删掉框选范围内的方块，后面的**往前接上**（不留空档）。\n'
            '先框选：在时间轴空白处拖一段。')
        self.btn_del_keep = QPushButton('删除留空')
        self.btn_del_keep.setIcon(QIcon(appstyle.trash_icon(15)))
        self.btn_del_keep.setIconSize(QSize(15, 15))
        self.btn_del_keep.setToolTip(
            '只删框选范围内的方块，后面的**原地不动**（那段变成空的）。')
        self.btn_sel_all = QPushButton('全选')
        self.btn_clear_sel = QPushButton('取消选区')
        self.btn_undo = QPushButton('↶  撤销')
        self.btn_undo.setToolTip('随时按 Ctrl+Z 都行（在谱面文本框里打字时除外，'
                                 '那时 Ctrl+Z 撤的是打字）')
        self.btn_clear_all = QPushButton('全删')
        for b in (self.btn_del_sel, self.btn_del_keep, self.btn_sel_all,
                  self.btn_clear_sel, self.btn_undo, self.btn_clear_all):
            b.setFixedHeight(30)
        self.btn_clear_all.setObjectName('danger')

        self.lbl_pos = QLabel('位置：0.00 秒')
        self.lbl_pos.setObjectName('pos')

        rowp = QHBoxLayout()
        rowp.addWidget(self.btn_play)
        rowp.addWidget(self.btn_stop)
        # （「回到开头」挪到左上角跟「手动演奏」作伴了 ——
        #   播放相关的按钮放一块才想得起来用）
        # ★ 「手动演奏」的开关已经挪到打击垫底下了（见 `pad_box`）★
        #   用户要的是**一个**开关，不是"开始 + 结束"两个键：
        #   点一下开、再点一下关。它跟打击垫是一件事，放一起才顺手。
        rowp.addSpacing(10)
        rowp.addWidget(self.btn_del_sel)
        rowp.addWidget(self.btn_del_keep)
        rowp.addSpacing(10)
        rowp.addWidget(self.btn_sel_all)
        rowp.addWidget(self.btn_clear_sel)
        rowp.addWidget(self.btn_undo)
        rowp.addWidget(self.btn_clear_all)
        rowp.addSpacing(10)
        rowp.addWidget(self.lbl_pos, 1)
        tlb.addLayout(rowp)

        # ★ 「写入位置」那个下拉框删了 ★ —— 用户：「写入位置跟红线走就行了，
        #   这个选项也可以清理掉」。
        #   它原来有两个选项：「时间轴播放头」和「谱面文本光标」。
        #   制谱的主路径本来就是"在时间轴上点一下定位 → 敲打击垫"，
        #   文本光标那条路等于让你在一堆数字里手动找位置，早就没人走。
        rowq = QHBoxLayout()
        self.lbl_sel = QLabel('没有选区')
        self.lbl_sel.setObjectName('mute')
        rowq.addSpacing(12)
        rowq.addWidget(self.lbl_sel, 1)
        tlb.addLayout(rowq)

        # ================= 底部 =================
        self.info = QLabel('')
        self.info.setWordWrap(True)
        self.info.setObjectName('dim')

        self.btn_save = QPushButton('保存')
        self.btn_save_as = QPushButton('另存为…')
        self.btn_sample = QPushButton('插入示例')
        self.btn_close = QPushButton('关闭')
        # 「保存」是这一排里的主操作 —— 给它一个能看出主次的样式
        # （以前靠内联 `font-weight:bold` 加粗，现在统一交给 QSS）。
        self.btn_save.setObjectName('primary')
        bottom = QHBoxLayout()
        bottom.addWidget(self.info, 1)
        bottom.addWidget(self.btn_sample)
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_save_as)
        bottom.addWidget(self.btn_close)

        root = QVBoxLayout(self)
        # ★ 「手动演奏」单开一行，放在最上面 ★
        #   用户：「这里按钮太小了，需要大的按钮，直接单开一个地方放」。
        #   它跟打击垫是一件事，可"开 / 关"这个动作值得一块显眼的地方：
        #   塞在打击垫底下时，它跟复选框、说明文字挤在一列里，
        #   第一眼根本看不出那是个开关。
        # ★ 旁边跟着「回到开头」★
        #   用户：「打谱器在左上角需要新增一个指定按钮」——
        #   确认是"定位 / 回到开头"。它原来混在下面那排小按钮里
        #   （全选 / 取消选区 / 撤销…），要用了得翻半天。
        #   播放相关的本来就该在一块儿，挪上来跟"手动演奏"作伴。
        top_row = QHBoxLayout()
        top_row.addWidget(self.btn_rec, 1)
        top_row.addWidget(self.btn_home)
        root.addLayout(top_row)
        # ★ 上面那一块给个像样的最小高度 ★
        #   打击垫长大了，压得太扁就白搭（它是用来弹的）。
        #   430 是按实测内容算的（大按钮搬走之后少了 40）：
        #   打击垫 240 + 两行复选框 52 + 写和弦按钮 30 + 一行说明 20
        #   + 边距间距 ≈ 390。给少了打击垫会溢出、跟下面的控件叠住。
        self.split_top.setMinimumHeight(430)
        root.addWidget(self.split_top, 0)
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

        # 恢复上次的两个拖动开关（默认都是不勾 = 完全自由）
        try:
            s = QSettings('strinova', 'piano-sheet-editor')
            self.chk_snap.setChecked(
                bool(s.value('drag_snap', False, type=bool)))
            self.chk_auto_lane.setChecked(
                bool(s.value('drag_auto_lane', True, type=bool)))
        except Exception:
            pass

        self._wire()

        # ★ 节拍器看门狗 ★
        #   用户反复报「节拍器……一会没打就停了」。节拍器本来就该一直在走 ——
        #   掉线是 bug、不是一种状态。判据只用**"播放头有没有在前进"**，
        #   不去追是哪条路径把它停了（那样追不完）。
        #   你主动停过（空格 / ⏹ 结束 / ▶ 从这里播）之后，看门狗不再插手。
        self._last_head = 0.0
        self._stuck = 0
        self._watch = QTimer(self)
        self._watch.setInterval(150)
        self._watch.timeout.connect(self._watch_clock)
        self._watch.start()

        if path and os.path.isfile(path):
            self.load_file(path)
        else:
            self.text.setPlainText(SAMPLE)
        self._refresh_from_text()

    # ---------------- 信号 ----------------

    def _wire(self):
        self.pad.note_clicked.connect(self._on_pad_click)
        # ★ 时间轴上点方块 → 试听 ★（要不要出声看那个勾选框）
        self.tl_edit.note_clicked.connect(self._on_note_preview)
        # （`self.pad.chord_clicked -> _on_pad_chord` 那根线跟着和弦一起删了。）
        self.chk_snap.toggled.connect(self._apply_drag_prefs)
        self.chk_auto_lane.toggled.connect(self._apply_drag_prefs)
        # ★ 一个开关：点一下开、再点一下关 ★
        #   接 `clicked` 而不是 `toggled` —— `clicked` 在状态切换**之后**
        #   发，槽里判 `_rec_armed` / `player.playing` 决定"开还是关"
        #   照常成立。（`clicked` 带的 `checked` 参数会被 PyQt 丢掉。）
        self.btn_rec.clicked.connect(self._toggle_record_mode)
        self.text.textChanged.connect(self._on_text_changed)
        self.tl_edit.changed.connect(self._on_timeline_changed)
        self.tl_edit.playhead_moved.connect(self._on_playhead)
        self.tl_edit.playhead_dropped.connect(self._on_playhead_dropped)
        self.tl_edit.seek_requested.connect(self._play_from_beat)
        self.tl_edit.undo_requested.connect(self._push_undo)
        # 按 Ctrl+Z 走的是**撤回**；`undo_requested` 是"改之前先存一份"，
        # 两者以前共用一个信号，结果 Ctrl+Z 变成了纯存档。
        self.tl_edit.undo_pressed.connect(self._undo_once)

        self.btn_play.clicked.connect(self._play_clicked)
        self.btn_stop.clicked.connect(self._stop_playback)
        self.btn_home.clicked.connect(
            lambda: (self.tl_edit.set_playhead(0.0), self._on_playhead(0.0)))

        # ★ 用 lambda 包一层，别把槽函数直接接上去 ★
        #   `clicked` 会带一个 `checked=False` 参数过来；
        #   直接接 `self._delete_selection` 的话，那个 False 会落到
        #   `ripple` 上 —— 点"靠齐"结果执行了"留空"，很难查。
        self.btn_del_sel.clicked.connect(
            lambda *_: self._delete_selection(ripple=True))
        self.btn_del_keep.clicked.connect(
            lambda *_: self._delete_selection(ripple=False))
        self.btn_sel_all.clicked.connect(self.tl_edit.select_all)
        self.btn_clear_sel.clicked.connect(self._clear_sel_all)
        self.tl_edit.multi_changed.connect(self._on_multi_changed)
        self.btn_undo.clicked.connect(self._undo_once)
        self.btn_clear_all.clicked.connect(self._clear_all)
        self.tl_edit.selection_changed.connect(self._update_sel_label)
        self.btn_save.clicked.connect(self.save)
        self.btn_save_as.clicked.connect(self.save_as)
        self.btn_sample.clicked.connect(self._insert_sample)
        self.btn_close.clicked.connect(self.close)

        # （「Shift 逐个挑 + 写和弦按钮」那两根线跟着和弦一起删了。）

    # ---------------- 打击垫 ----------------

    def _arm_fire(self):
        """预备中按下的**第一个**音 —— 从这一刻开始计时。

        用户：「点按一下然后开始按音频按键就会开始」。

        ★ 起点 = 红线现在的位置，**不再写死 0** ★
          用户报的：「我把红线移到后面他会从头开始来」。
          原来这里固定调 `_play_from_beat(0.0)`，于是不管你把红线
          拖到哪儿，按第一个音的一瞬间时间轴都会被**推回 0** ——
          想从中间**接着往下录**永远做不到。
          起点跟着红线走之后就两头都对了：
            · 红线在开头 → 老行为（第一个音落在 0 秒附近）
            · 红线先拖到 30 秒 → 第一个音就落在 30 秒，接着那儿往下录
        """
        if not self._rec_armed:
            return
        self._rec_armed = False
        self._rec_from = max(0.0, float(self.tl_edit.playhead))
        # ★ 延到下一轮事件循环再启动时间轴 ★
        #   调用这一刻，要写的那个音**还没进模型**，而 `play_from()`
        #   在空谱面上会直接返回 —— 那样时间轴根本不会走，
        #   整段记录就全堆在 0 秒了。
        QTimer.singleShot(0, self._rec_start)
        # 真的开始录了 —— 变红
        self._set_recording(True)
        self.btn_rec.setText('演奏中…')
        self.lbl_pos.setText(
            '手动演奏中… 从 <b>%.2f</b> 秒开始（红线在哪儿就从哪儿录），'
            '再点一下「手动演奏」停' % self._rec_from)

    def _rec_start(self):
        # `_play_from_beat` 会把时钟标成"该走了"，看门狗从此守着它 ——
        # 记录全靠这条时间参考，它要是悄悄停了，后面写的音全错位。
        # 静音：只走时间、不出声。起点用按下第一个音时的红线位置。
        self._play_from_beat(self._rec_from, muted=True)

    def _on_note_preview(self, pitch: str):
        """在时间轴上点了个方块 —— 勾了「点轨道音符听音」就出个声。

        ★ 为什么需要这一下 ★
          改谱子时最常用的动作就是"挪完一个音，想确认它还是原来那个"。
          没有这个，就得按播放等它走到，或者去打击垫上找那个键。
          默认**不勾**：不勾的时候点方块只是选中 / 拖动，安安静静。
        """
        if self.chk_preview.isChecked():
            self.pad.player.play(pitch)

    def _on_pad_click(self, pitch: str):
        # ★ 先告诉浮窗"这个键被点了" ★
        #   放在最前面 —— 不管后面写不写进谱面（你可能只是在试弹，
        #   或者没勾「同步写入谱面」），这一点都该立刻有反馈。
        self.pad_hit.emit([pitch])
        if not self.chk_insert.isChecked():
            return
        self._arm_fire()
        gap = GAP_DEFAULT

        # ★ 一律写在**红线那儿**（有选区就落选区起点）★
        #   用户：「写入位置跟红线走就行了，这个选项也可以清理掉」。
        #   原来这里判 `cmb_write` 是 'head' 还是 'cursor'，两条路各写一份；
        #   现在只剩这一条，缩进也跟着退了一层。
        if self.model is None:
            return
        rng = self.tl_edit.selection_beats()
        at = rng[0] if rng else self.tl_edit.playhead

        # ★ 时间轴谱面：每次按都是一个**独立的新方块**，别的不动 ★
        #   用户：「连续按了还是连在一块……不需要合并，分别到不同的
        #   轨道上就行」。所以这里**不查"那儿有没有音"**，直接放下；
        #   时间撞上了也没关系 —— `auto_lanes()` 会把它们分到不同轨道。
        if self.model.free:
            self._push_undo()
            n = self.model.add_free_note(at, pitch)
            # ★ 永远落在「当下」，不推 ★
            #   用户：「再打他会按照跳着的格式排，1 2 3 这样子，
            #   **不是上下堆叠**，这样子是不对的」——
            #   推间距（`+ gap`）会把音排成一条长链；他要的是落在当下，
            #   撞在一起就由 `auto_lanes()` 分到不同轨道（上下堆叠）。
            self._sync_model(n.start)
            self.lbl_pos.setText(
                '放下 <b>%s</b>（%.2f 秒）'
                % ('+'.join(n.pitches), n.start * SPB))
            return

        # ★ 那一拍已经有音了？那就**叠上去变成和弦**（同时发声），
        #   不占新的时间 —— 这就是「一个时间里放好几个音」的入口。
        target = self.model.note_starting_at(at, tol=CHORD_TOL)
        if (target is not None and pitch not in target.pitches):
            self._push_undo()
            self.model.add_pitch(target, pitch)
            self._sync_model(target.start)
            self.lbl_pos.setText(
                '叠成和弦 <b>%s</b>（%.2f 秒，一起响）'
                % (target.label, target.start * SPB))
            return

        self._push_undo()
        toks = tokens_for(pitch, gap)
        idx = self.model.rest_index_after_beat(at)
        self.model.insert_tokens(idx, toks)
        total = sum(parser.token_duration(t)[0] for t in toks)
        self._sync_model(at + total)

    # ★ `_on_pad_chord()` 已删除 ★ —— 用户：「和弦功能用不到」
    #
    #   它是"打击垫上一次按下好几个键"的落笔入口：把一串音高用 `&`
    #   连成一个和弦 token（`1&3&5`），或者叠到已有的那个音上。
    #   打击垫那三种多选（拖动划过 / Shift 框选 / Shift 逐个挑）全都
    #   通向它，整套一起删了。
    #
    #   谱面里本来就有的和弦照样能显示、能试听、能编辑 —— 那是
    #   `parser` / `EditModel` / 时间轴那一侧的事，跟录入这条路无关。

    def _sync_model(self, head: float):
        """把模型改动同步到文本 / 播放器 / 时间轴，播放头停在 head 拍。"""
        self._syncing = True
        self.text.setPlainText(self.model.rebuild())
        self._syncing = False
        self.player.set_model(self.model)
        self.tl_edit.set_model_keep_head(self.model, head)
        self._tl_dirty = True               # 浮窗那份 Timeline 作废
        self._update_info(None)

    # ---------------- 给谱面浮窗用 ----------------

    def overlay_timeline(self):
        """当前谱面，包成浮窗能画的 Timeline（带缓存）。

        ★ 只在模型真的变了才重建 ★
          浮窗每 12 ms 来问一次"现在该打哪些键"，每次都重建一遍
          几百个音的 Timeline 是纯浪费。制谱器里所有改动都会经过
          `_sync_model` / `_refresh_from_text` / `_on_timeline_changed`，
          那三处把 `_tl_dirty` 立起来就够了。
        """
        if self.model is None:
            return None
        if self._tl_dirty or self._tl_cache is None:
            try:
                # ★ 先把「拍」换算成「秒」再交出去 ★
                #   这一句原来是直接把 `self.model.notes` 递进去的 ——
                #   而 `EdNote.start` 是**拍**，`timeline_from_notes()` 又把它
                #   原样当秒用，于是浮窗那条时间轴被整个**放大了一倍**，
                #   而喂进去的播放位置是真实秒 —— 越弹越落后。
                #   实测数据和它造成的"落后两个格"见 `_SecNote` 的注释。
                notes = [_SecNote(n, SPB) for n in self.model.notes]
                self._tl_cache = timeline.timeline_from_notes(
                    notes, bpm=self._bpm())
            except Exception:
                return self._tl_cache
            self._tl_dirty = False
        return self._tl_cache

    # ---------------- 轨道数 ----------------

    def _on_lanes_toggled(self, on: bool):
        """「12 轨」按钮 —— 切轨道数，顺手把滚动区高度跟着改。

        用户：「这里面新增一个按钮开启 12 轨，正常 6 轨」。

        ★ 高度必须跟着改，不然等于没切 ★
          `tl_scroll.setMinimumHeight()` 是按 `轨数 × ROW_H` 算的。
          轨数翻倍而高度不动的话，下面那 6 条会被滚动区裁掉 ——
          用户按了按钮，看到的还是 6 条，只会以为按钮坏了。
          （滚动区本身能滚，但"能滚"和"一眼看到"是两回事：
           这里要的是铺开，不是让用户去滚。）

        ★ 切小的时候音符会被收拢 ★
          见 `TimelineEditor.set_lanes()`：落在 7~12 轨的方块会被压到
          第 6 轨，不然它们会落到轨道区外面、画不出来也点不到。
        """
        n = LANES_MAX if on else LANES
        if not self.tl_edit.set_lanes(n):
            return                       # 轨数没变（比如本来就是这个数）
        self.tl_scroll.setMinimumHeight(RULER_H + n * ROW_H + 24)
        self.btn_lanes.setToolTip(
            ('现在是 12 轨（点一下收回 6 轨）\n'
             '切回 6 轨时，第 7~12 轨上的方块会被收拢到第 6 轨。' if on else
             '不按（默认）：时间轴 6 条轨道。\n'
             '按下去：铺 12 条 —— 方块时间上叠得厉害时能摊得更开。\n'
             '（切回 6 轨时，原来摆在第 7~12 轨上的方块会被收拢到第 6 轨。）'))

    # ---------------- 拖动偏好 ----------------

    def _apply_drag_prefs(self):
        """把两个拖动开关发给时间轴。

        ★ 模型是**每次改文本都重建**的 ★
          （`_refresh_from_text` 里 `self.model = EditModel(sheet)`）
          所以不能只在初始化时设一次 —— 每次重建之后都要重新灌一遍。

        ★ 这里没有"并成和弦"了 ★ 用户：「不需要合并的」。
          撞上了只是挨着，不会再被吃掉变成和弦；挤不开时由
          `auto_lane_on` 把它排到别的轨道上。
        """
        snap = False
        auto_lane = True
        try:
            snap = self.chk_snap.isChecked()
            auto_lane = self.chk_auto_lane.isChecked()
        except Exception:
            return
        try:
            self.tl_edit.snap_on = snap
            self.tl_edit.auto_lane_on = auto_lane
        except Exception:
            pass
        if auto_lane:
            self.tl_edit.auto_lanes_now()
        try:
            s = QSettings('strinova', 'piano-sheet-editor')
            s.setValue('drag_snap', snap)
            s.setValue('drag_auto_lane', auto_lane)
        except Exception:
            pass

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
        """在谱面文本框的**光标处**插一段文字。

        ★ 现在没有生产代码在调它了 ★ —— 用户：「写入位置跟红线走就行了」。
          「写入位置」那个下拉框删掉之后，"敲打击垫写进文本框光标处"
          这条路就没了（打击垫的音现在一律落在时间轴红线上）。
          留着是给"以后想再加一条写文本的快捷键"备着 —— 它只有三行，
          比需要时重新琢磨一遍光标怎么搬回来划算。
        """
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
        bpm = self._bpm(sheet)
        # ★ 时间轴横轴的单位是秒 ★
        #   现在只有一种谱面模型（每个音带绝对秒），所以换算就一个常数。
        try:
            self.tl_edit.spb = SPB
        except Exception:
            pass
        self._apply_drag_prefs()          # 新模型要重新灌一遍拖动偏好
        if keep_head is None:
            self.tl_edit.set_model(self.model)
        else:
            self.tl_edit.set_model_keep_head(self.model, keep_head)
        self.player.set_model(self.model)
        self.player.bpm = bpm
        self._tl_dirty = True               # 浮窗那份 Timeline 作废
        self._update_info(sheet)
        self._push_to_overlay()             # 一打开就让浮窗对上位置

    def _on_timeline_changed(self):
        if self.model is None:
            return
        self._syncing = True
        self.text.setPlainText(self.model.rebuild())
        self._syncing = False
        self.player.set_model(self.model)
        self._tl_dirty = True               # 浮窗那份 Timeline 作废
        self._update_info(None)

    def _update_info(self, sheet=None):
        if self.model is None:
            self.info.setText('')
            return
        parts = [self.model.stats()]
        if sheet is not None:
            tl = timeline.Timeline(sheet)
            # ★ 别再报一遍秒数 ★
            #   自由格式下 `stats()` 已经说了「共 X 秒」，这里再来一个
            #   「约 Y 秒」就是同一件事说两遍 —— 界面上会出现
            #   「8.50 秒 · 约 8.5 秒」这种啰嗦。老格式的 `stats()`
            #   报的是拍数，那才需要补一个秒。
            if not self.model.free:
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

    def _play_from_beat(self, beat: float, muted: bool = False,
                        keep_fired: bool = False):
        if self.model is None:
            return
        # 明确的开播动作 —— 从这一刻起时钟"该走了"，看门狗开始管它
        self._clock_should_run = True
        self.player.bpm = self._bpm()
        # ★ `muted=True` = 只走时间、不出声（记录模式用）★
        self.player.muted = muted
        self.tl_edit.set_playhead(beat)
        # `keep_fired` 由看门狗那条路径传 True（见 `_watch_clock`）：
        # 拉起卡住的时钟时**不许重放已经播过的音**。
        self.player.play_from(beat, keep_fired=keep_fired)

    def _stop_playback(self):
        """停掉时钟（停止按钮 / 记录结束都走这儿）。"""
        self.player.stop()
        self.player.muted = False
        self._clock_should_run = False
        self.lbl_pos.setText('已停在 %.2f 秒（点「▶ 从这里播」接着走）'
                             % (self.tl_edit.playhead * self.tl_edit.spb))

    def _toggle_record_mode(self):
        """⏺ 点一下 = 进入预备；再点（或按「⏹ 结束」）= 停。

        用户：「点按一下然后开始按音频按键就会开始，然后有一个结束按键」。

        ★ 起点跟着红线走 —— 见 `_arm_fire` ★
          用户：「我把红线移到后面他会从头开始来」。
        """
        if self._rec_armed or self.player.playing:
            # 已经在等第一个音 / 已经在录 / 正在听谱子 —— 这一下都是"关掉"
            self._stop_record()
            return
        self._clock_should_run = False   # 等待期间时钟停着，别自己跑起来
        self.player.stop()
        self._rec_armed = True
        self.btn_rec.setChecked(True)
        # 待命态 —— 蓝的（还没开始录），等第一个音按下去才变红
        self._set_recording(False)
        self.btn_rec.setText('等第一个音…')
        self.lbl_pos.setText(
            '手动演奏：按第一个音就开始计时，它会落在 <b>%.2f</b> 秒'
            '（想从别处开始，先把红线拖过去）' % self.tl_edit.playhead)

    def _set_recording(self, on: bool):
        """切换「手动演奏」按钮的第三态（在录 / 没在录）。

        ★ 三态：关（深蓝）／开着待命（亮蓝）／正在录（红）★
          用户：「这个在暂停的时候要自动变换蓝色手动演奏按键」。
          "等第一个音"那会儿**还没开始录** —— 一直顶着一块红，
          看着像"已经在录了"，其实是待命。红色只留给真的在录那一段。
          样式在 `appstyle` 的 `QPushButton#record[recording="true"]`。
        """
        self.btn_rec.setProperty('recording', bool(on))
        # ★ Qt 的属性选择器不会自己重算 ★
        #   改完属性必须 unpolish + polish 一次，样式才会跟上；
        #   少了这两步，属性变了颜色纹丝不动（这个坑很常见）。
        st = self.btn_rec.style()
        st.unpolish(self.btn_rec)
        st.polish(self.btn_rec)
        self.btn_rec.update()

    def _stop_record(self):
        """关掉手动演奏。已经写下来的音都留着。"""
        self._rec_armed = False
        self._stop_playback()
        self.btn_rec.setChecked(False)
        self._set_recording(False)
        self.btn_rec.setText('手动演奏')
        self.lbl_pos.setText('手动演奏已关闭（写下来的音都留着）')

    def _toggle_play(self):
        """空格：正在播就停，没播就从播放头接着播。

        「接着播」是靠播放头实现的 —— 播放时播放头一直跟着走
        （`_on_player_tick` 每 12 ms 更新一次），停下时它就停在原地，
        于是再按空格是从刚才断开的地方继续，而不是回到开头。
        """
        if self.player.playing:
            self._stop_playback()
        else:
            # 有选区就从选区起点（见 `_play_start_beat`），否则从红线接着播
            self._play_from_beat(self._play_start_beat())

    def keyPressEvent(self, event):
        """★ 空格 = 播放 / 停止 ★（和主界面上的空格一致）

        ★ 为什么用 keyPressEvent 而不是 QShortcut ★
          一开始想照抄主界面那句
          `QShortcut(QKeySequence(Qt.Key.Key_Space), self, ...)`。
          但 QShortcut 在窗口范围内**优先级高于输入框**，会把空格
          从谱面文本框手里抢走 —— 而记谱法是靠空格排版的
          （帮助里写着「空格换行随意」），抢走就等于打不出空格了。
          改写成 keyPressEvent 之后，焦点在文本框里时事件**根本到不了
          这里**（QPlainTextEdit 自己就把空格吃了），正好是想要的语义：
          在谱面上打字，空格是空格；在打击垫/时间轴上，空格是播放。

          能收到这个事件，是因为 `TimelineEditor` 对不认识的键都落到
          `super().keyPressEvent()`（等于忽略），事件于是沿父链冒泡上来。

        带修饰键的空格（Ctrl/Alt/Shift+空格）一律不拦，留给别的用途。
        """
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        # ★ 硬保险：焦点只要在输入框里，这个函数就什么都不管 ★
        #   下面每个分支本来都查了一遍 `focusWidget()`，但实测**还是漏了**
        #   （在谱面文本框里按空格，字打进去了，播放却也被触发了）。
        #   与其逐条排查，不如在最前面一刀切掉 —— 在文本框里打字时，
        #   空格、Ctrl+Z 本来就该是文本框自己的事。
        fw = self.focusWidget()
        if isinstance(fw, (QPlainTextEdit, QTextEdit, QLineEdit)):
            super().keyPressEvent(event)
            return

        # ★ Ctrl+Z = 撤回上一步 ★
        #   时间轴自己就认这个键（在 `TimelineEditor.keyPressEvent` 里），
        #   但那只在焦点正好落在时间轴上时管用 —— 点过打击垫、点过按钮
        #   之后焦点就跑了，再按 Ctrl+Z 一点反应都没有。
        #   这里补一道兜底：只要焦点不在输入框里，Ctrl+Z 一律走
        #   制谱器自己的撤销栈（栈里存的是每一步之前的谱面文本快照）。
        if event.key() == Qt.Key.Key_Z and ctrl:
            fw = self.focusWidget()
            if not isinstance(fw, (QPlainTextEdit, QTextEdit, QLineEdit)):
                self._undo_once()
                event.accept()
                return

        if (event.key() == Qt.Key.Key_Space
                and not (mods & (Qt.KeyboardModifier.ControlModifier
                                 | Qt.KeyboardModifier.AltModifier
                                 | Qt.KeyboardModifier.ShiftModifier))):
            fw = self.focusWidget()
            if not isinstance(fw, (QPlainTextEdit, QTextEdit, QLineEdit)):
                self._toggle_play()
                event.accept()
                return
        super().keyPressEvent(event)

    # ---------------- 区域操作 ----------------

    # ★ 选区循环播放已经删掉 ★
    #   用户：「循环选区删掉，删除选区可以保留」。
    #   它作为"选区的用途"只活了一小会儿 —— 先是"选区就该循环播放"
    #   （「选区功能应该是在选区内循环播放」），然后发现循环会跑出
    #   选区（「只是循环播放选区内，这个还会超出选区」），
    #   最后决定整个不要。
    #   底层的循环能力（`EditPlayer` 的 `loop` / `looping`）留着 ——
    #   它是干净的区间循环实现，`_smoke_loop.py` 还在盯着它，
    #   将来要做"听这一段"再挂个入口就行，不用重写。
    #   选区现在只剩一个用途：**圈出要删的范围**。

    def _delete_selection(self, ripple: bool = True):
        """删掉选区内的块。`ripple=True` 后面往前接上，`False` 留在原地。"""
        rng = self.tl_edit.selection_beats()
        if not rng:
            self.lbl_pos.setText('没有选区可删 —— 在时间轴空白处拖一下框选')
            return
        n = self.tl_edit.delete_selection(ripple=ripple)
        self.lbl_pos.setText(
            '已删除选区内的 %d 个块%s'
            % (n, '（后面的往前接上了）' if ripple else '（后面的留在原地）'))

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

    def _insert_sample(self):
        """插入示例 —— 这也是一次「改动」，得先进撤销栈。

        ★ 以前这里是 `lambda: self.text.setPlainText(SAMPLE)` ★
          `setPlainText` 会把 QPlainTextEdit 自己那份撤销历史**清空**，
          而制谱器这条栈又没记 —— 于是点了「插入示例」之后
          Ctrl+Z 完全没反应（用户报的「有的改动无法撤回」之一）。
        """
        self._push_undo()
        self.text.setPlainText(SAMPLE)

    # （`_on_picked_changed()` 已删除 —— Shift 逐个挑那套跟着和弦一起没了。）

    def _clear_sel_all(self):
        """「取消选区」= 范围选区 + Shift 逐个多选，一起清掉。"""
        self.tl_edit.clear_all_picks()
        self._update_sel_label()

    def _on_multi_changed(self, _n: int):
        self._update_sel_label()

    def _update_sel_label(self):
        rng = self.tl_edit.selection_beats()
        multi = len(getattr(self.tl_edit, 'multi', None) or [])
        parts = []
        if rng:
            # ★ 只说它的本职 ★
            #   框选 = 圈出要删掉的那一段（用户后来把"循环播放"删了，
            #   见 `_delete_selection` 上面的说明）。
            parts.append('框选 %.2f ~ %.2f 秒（用「删除并靠齐 / 删除留空」删掉）'
                         % (rng[0] * SPB, rng[1] * SPB))
        if multi:
            parts.append('Shift 挑了 %d 个（可整组拖 / Delete 一起删）' % multi)
        if parts:
            self.lbl_sel.setText(' · '.join(parts))
        else:
            self.lbl_sel.setText(
                '没有选区（空白处拖 = 框选；Shift 点方块 = 逐个多选）')

    def _on_player_tick(self, beat: float):
        # ★ 你手正按在时间轴上的时候，播放器不许改播放头 ★
        #   播放器每 12 ms 报一次位置；照单全收的话，你刚把红线拖到
        #   30 秒，下一帧就被拽回播放器那边 —— 手感就是"拖不动"，
        #   松手后还被"从头开始"。
        #   松手时 `playhead_dropped` 会让播放器从你放下的位置接着走。
        if self.tl_edit.mouse_held:
            return
        self.tl_edit.set_playhead(beat)
        # ★ 跟着走 ★ 节拍器一直往前，不跟随的话红线一会儿就跑出屏幕，
        #   看起来也像"停下来了"
        try:
            self.tl_edit.follow_playhead()
        except Exception:
            pass
        self._on_playhead(beat)

    def _on_player_done(self):
        # 播到曲子末尾（或选区末尾）自己停了 —— 时钟从此"不该走"，
        # 看门狗别把它又拉起来。
        self._clock_should_run = False
        self.lbl_pos.setText('位置：播放完毕')

    def _on_playhead(self, beat: float):
        total = self.model.total_beats if self.model else 0.0
        self.lbl_pos.setText('位置：%.2f 秒 / 共 %.2f 秒'
                             % (beat * SPB, total * SPB))
        self._push_to_overlay()

    def _push_to_overlay(self):
        """把"我现在到第几秒了"报给控制台（它转给谱面浮窗）。

        ★ 挂在 `_on_playhead` 上是有意的 ★
          这个函数被**所有**播放头变动调用：时钟每 tick 一次、
          你拖红线、点空白处定位 —— 一处挂上就全覆盖了。
        """
        try:
            self.timeline_tick.emit(
                float(self.tl_edit.playhead) * float(self.tl_edit.spb))
        except Exception:
            pass

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
        # ★ 没写扩展名就补一个 `.txt` ★
        #   `core/paths.py::all_sheets()` 只认 `*.txt`。存成
        #   `sheets/新谱面`（没有后缀）的话，侧栏、下拉框、浮窗的选曲菜单
        #   **全都列不出它** —— 用户看到的是"保存了但哪儿都找不到"，
        #   只能自己进文件夹确认文件其实在。
        #   Windows 的保存对话框在「所有文件」那一档不会自动补后缀，
        #   所以必须自己来。
        if not path.lower().endswith('.txt'):
            path += '.txt'
        self.path = path
        return self.save()

    def showEvent(self, event):
        """★ 打开时**不**自动播放 ★

        用户：「点开谱面会自动播放而且没有声音」。
        以前这里挂了一个 `_boot_clock()`：一打开就用**静音**模式把时间轴
        跑起来，还说"节拍器本来就该一直走"。可原话「一直走就行」是在
        **记录模式**的语境里说的 —— 打开一份谱面就自己跑起来、还听不见
        声音，那看着就是坏了。

        现在时间轴只在三个时候走：
          · 按空格 / 「▶ 从这里播」→ 出声试听
          · 点「⏺ 节拍记录」再按第一个音 → 静音走时间（当记录的时间参考）
        其余时候它老老实实停着。
        """
        super().showEvent(event)

    def _play_start_beat(self) -> float:
        """按下播放时该从哪儿开始（拍）。

        ★ 有选区就从**选区起点**开始 ★
          用户：「从右往左选的时候按空格不会从选框的头部开始播放
          而是从红线位置往后，这个需要修复」。
          框选表达的是"我关心这一段"—— 划好了选区却从别处播，
          那一下框选就白做了。
          `selection_beats()` 已经把两端排过序，所以不管是左往右拖
          还是右往左拖，起点都是**时间小的那一端**（= 头部）。

        ★ 没有选区才是"从红线接着播" ★（那是原来唯一的行为）
        """
        rng = self.tl_edit.selection_beats()
        if rng:
            return rng[0]
        return self.tl_edit.playhead

    def _play_clicked(self):
        """「▶ 从这里播」：这次是要<b>听谱子</b>（会出声）。

        起点交给 `_play_start_beat()` —— 有选区就从选区起点。
        """
        self._play_from_beat(self._play_start_beat())

    def _watch_clock(self):
        """★ 看门狗：**该走的时候**必须一直在走 ★

        用户反复报「一会没打就停了」。与其去追播放器里**哪条路径**把它停了
        （曲末判定、停止信号、换模型…… 追不完），不如换一个**不用解释
        原因**的判据：

            只要**播放头没在前进**，就把它拉起来。

        这才是稳的 —— "播放器说自己在播"和"时间真的在走"是两回事。

        ★ 但它绝不负责"启动" ★
          判据里那个"播放头没动"在时钟**本来就是停的**时候同样成立，
          于是它以前会自己把静音时钟拉起来 —— 那正是用户说的
          「点开谱面会自动播放而且没有声音」。
          所以先看 `_clock_should_run`：你没按过播放/记录，它一概不管。
        """
        if not self._clock_should_run:
            self._stuck = 0
            self._last_head = self.tl_edit.playhead
            return
        if self._rec_armed:
            # 预备中（等你按第一个音）—— 时钟此时是停的，别去动它
            self._stuck = 0
            self._last_head = self.tl_edit.playhead
            return
        # 你手正按在时间轴上（拖红线 / 定位）—— 别在这时候把时钟拉起来，
        # 不然会以你拖动中的位置为准重新起跑，看着就是"跳了一下"。
        if self.tl_edit.mouse_held:
            self._stuck = 0
            self._last_head = self.tl_edit.playhead
            return
        # ★ 播放器自己都停了，就别去"救活"它 ★
        #   用户：「完全按照时间轴来，去掉节拍这个东西」。
        #   以前这里不看 `player.playing`，只看"播放头没在动"就拉起来 ——
        #   于是你按过播放、又按了停止，再点一下时间轴定位，
        #   三百毫秒后时钟自己又跑起来（试听模式下还会**真的出声**）。
        #   判据改成"它本来在播、可是时间没走"才算卡住。
        if not self.player.playing:
            self._stuck = 0
            self._last_head = self.tl_edit.playhead
            return
        head = self.tl_edit.playhead
        moved = abs(head - self._last_head) > 1e-9
        self._last_head = head
        if moved:
            self._stuck = 0
            return
        # 一次不动可能只是两帧挨得太近，连续两次才算真卡住
        self._stuck += 1
        if self._stuck < 2:
            return
        self._stuck = 0
        # 拉起来时**保持原来的静音状态**：记录中是静音，试听时是该出声的
        # ★ 而且**不许重放已经播过的音** ★
        #   这里是"救活一个卡住的时钟"，`head` 就是刚刚播到的地方 ——
        #   照常清空 `_fired` 的话，下一帧会把 head 之前每一个音重新
        #   emit 一遍（听感：一串和弦齐鸣）。所以传 `keep_fired=True`。
        self._play_from_beat(head, muted=self.player.muted, keep_fired=True)

    def closeEvent(self, event):
        # ★ 关窗必须把定时器停掉 ★
        #   这个对话框的 parent 是控制台主窗口 —— `exec()` 返回之后
        #   Qt 对象仍被父窗口持有，定时器**不会**自己停：
        #   `_blink`(40ms) 会一直重绘打击垫、`_watch`(150ms) 会一直访问
        #   时间轴，关掉制谱器之后 CPU 照样被吃掉。
        self._clock_should_run = False
        self._blink.stop()
        self._watch.stop()
        # 防抖是单发的，但关窗那一刻可能正有一次在途 —— 一起停掉，
        # 免得它再回调一次 `_refresh_from_text()`。
        self._debounce.stop()
        self.player.stop()
        super().closeEvent(event)
        # ★ 补发一发 `finished` ★
        #   控制台是用 `dlg.finished.connect(_on_closed)` 来接管"关掉之后
        #   该干什么"的：把浮窗还回控制台自己那份谱面、刷新曲库、
        #   如果刚才保存过就重新载入它。
        #
        #   可这个对话框从头到尾**没有调过 `done()` / `accept()` /
        #   `reject()`** —— 而 `QDialog.finished` 只在 `done()` 里发。
        #   于是那条连接是一次都没响过的**死线**：
        #     · 关掉制谱器之后 `_editor` 还指着这个已经关掉的窗口，
        #       控制台以为它还开着（所以再点「编辑谱面」还能开出第二个）
        #     · 在制谱器里改完保存，曲库不刷新、控制台也不载入新谱面，
        #       用户回到控制台看到的还是旧的
        #   在这里显式补一发，那条路才真的通。
        #   （用 `event.isAccepted()` 挡一下：万一将来有人 reject 掉关闭，
        #     就不该报"已经关了"。）
        if event.isAccepted():
            self.finished.emit(0)
