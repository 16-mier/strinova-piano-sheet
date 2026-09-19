# -*- coding: utf-8 -*-
"""时间轴编辑器 —— 剪辑软件那种：几条自由轨道 + 等宽方块 + 区域选择。

横向 = **秒**（不是拍）。
       用户：「也不需要拍子，全部按照时间轴来算」—— 歌的节奏快慢不定，
       数拍子对不上，能确定的只有"第几秒"。内部仍然按拍存（谱面文本
       就是记谱法），画和拖的时候用 `spb`（秒/拍）换算，见 `set_playhead`。
纵向 = **6 条自由轨道**（不是钢琴卷帘的"一个音高一行"）——
       音高写在方块上（`1`、`2'`、和弦写 `1&3`），轨道只用来把
       **时间上挨得太近的方块上下错开**，免得互相压住看不清。
       所以上下拖 = 换层，跟音高、跟发声都没关系。

★ 方块是**等宽**的（`NOTE_W_BEAT` 拍）★
  时值仍然存在（它决定演奏间隔），但**不再按比例画成宽度**，
  也没有"拖右边缘拉长"这个操作了 —— 用户：「不用拉长，
  每个声音长度是一样的」。

**一个音符块 = 一个方块**。和弦也只画一个（写 `1&3`），
所以"点一下只选中一个"是天然的。

操作一览
    拖方块            左右 = 改时间（**不带动后面的方块**）
                      上下 = 换轨道（错开重叠）
    Alt + 拖          把某个音从和弦里拆出来单独挪
    点方块            选中 + 试听
    双击方块          从它这里开始播
    右键点方块        把那个音从和弦里拿掉（单音块就是整块删掉）
    按住红线拖        挪播放头
    点一下空白        播放头跳过去
    空白处按住拖      框选一段区域（拖到视野边缘会自动滚）
    Ctrl + 滚轮       缩放
    左右方向键        微调选中方块的时间（1/4 拍，只动它自己）
    Delete            有选区就删选区，否则删选中的块
    Ctrl + Z          撤销
    Ctrl + A          全选
    Esc               取消选区
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainter, QPen,
                         QPolygonF)
from PyQt6.QtWidgets import QWidget

from core import layout
from core.edit_model import EditModel, EdNote

from . import theme as T

# ★ 轨道 ★
#   原来是 16 行、每行一个音高（钢琴卷帘）。用户要的是「剪辑软件」那种：
#   **几条自由轨道**，上下拖只是换个"层"避免方块挤在一起，
#   音高写在块上就够 —— 所以行和音高**不再绑定**。
#
#   ★ 2026-09：从"固定 6 条"变成"默认 6 条、可以切 12 条" ★
#     用户：「这里面新增一个按钮开启 12 轨，正常 6 轨」。
#     所以这个常量现在的身份是**默认值**，真正在用的是实例属性
#     `self.lanes`（见 `TimelineEditor.__init__` 和 `set_lanes()`）——
#     下面所有算式都读 `self.lanes`，不再读这个名字。
#     `ui/editor.py` 那边要挂 6/12 的开关。
LANES = 6           # 默认轨道数（正常 6 轨）
LANES_MAX = 12      # 上限（用户要的"开启 12 轨"）
ROW_H = 40          # 每条轨道的高度（行少了，可以给高一点）
HEADER_W = 64       # 左边轨道头宽度

# ★ 顶部时间标尺 ★
#   用户：「可以和 PR 的轨道一样啊」。
#   PR 的时间轴是**分区**的：
#       左上角   ┌ 时间标尺（横轴刻度 + 播放头把手）
#       轨道头   │ 轨道区（放素材）
#   于是"拖播放头"和"框选素材"是两个**不重叠**的区域 ——
#   点标尺就是挪红线，绝不会误触发框选；反过来在轨道区里
#   拖也不会把红线挪走。以前两者挤在同一块地方，
#   只能靠"离红线 6 像素内算抓住"这种判定去猜，很难用。
RULER_H = 26
SNAP = 0.25         # 方向键微调用的步长（拍）
SNAP_SEC = 0.05     # 「拖动吸附」打开时的吸附精度（**秒**）


def _fmt_sec(sec: float) -> str:
    """秒 -> 刻度标签。不到一分钟写 `1.5s`，超过就写 `1:30`。"""
    if sec < 60:
        return '%gs' % round(sec, 2)
    m = int(sec // 60)
    return '%d:%02d' % (m, round(sec - m * 60))

# ★ 块的宽度固定 ★
#   用户：「不用拉长，每个声音长度是一样的」。
#   时值**仍然存在**（它决定演奏间隔），只是不再按比例画成宽度。
#   0.8 拍宽 —— 比常见的最小间隔（0.25 拍）宽得多，所以相邻的块看起来
#   会挨在一起甚至压上，这正是需要「上下换轨道」错开的场合。
NOTE_W_BEAT = 0.8

HEAD_GRAB = 6       # 红线左右多少像素内算「抓住了红线」
BAND_MIN = 7        # 位移超过这么多像素才算框选（不然就是「点一下挪播放头」）

# ★ 时间轴最少留这么长（秒）★
#   用户：「节拍器还是有 bug，如果后面停下来不会继续走……
#   需要预设 3 分钟长度先」。
#   节拍器要一直走，就必须有一段**够长的空间**让它走 —— 不然
#   `total_sec` 只算到"最后一个音的末尾"，可滚动范围也就到那儿，
#   走到头看起来就是"停下来了"（其实还在走，只是没地方显示了）。
MIN_SPAN_SEC = 180.0
HOLD_MS = 280       # 红线按住不动这么久 = 长按 → 转成框选
SCROLL_STEP = 22    # 拖到视野边缘时每次自动滚多少像素
EDGE_ZONE = 26      # 离视野边缘这么近就开始自动滚

SEL_FILL = QColor(86, 168, 255, 58)
SEL_EDGE = QColor(130, 195, 255, 190)
HEAD_COLOR = QColor(255, 96, 96)


class TimelineEditor(QWidget):
    """钢琴卷帘编辑器（支持框选）。"""

    note_clicked = pyqtSignal(str)      # 点了某个音（用于试听）
    changed = pyqtSignal()              # 模型被改过
    playhead_moved = pyqtSignal(float)  # 播放头挪到第几拍
    playhead_dropped = pyqtSignal(float)  # 拖红线松手（正在播就从新位置接着播）
    seek_requested = pyqtSignal(float)  # 请求从某拍开始播
    selection_changed = pyqtSignal()    # 选区变了
    undo_requested = pyqtSignal()       # 改谱之前先让外面存一个撤销点
    undo_pressed = pyqtSignal()         # ★ 用户按了 Ctrl+Z，要**撤回**（不是存档）★
    multi_changed = pyqtSignal(int)     # Shift 逐个多选的块数变了

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: EditModel | None = None
        # ★ 横轴的单位是**秒**，不是拍 ★
        #   用户：「也不需要拍子，全部按照时间轴来算」——
        #   歌的节奏快慢不定，数拍子对不上，能确定的只有"第几秒"。
        #   内部仍然按拍存（谱面文本就是记谱法，`-` 就是一拍），
        #   这里只是拿 `spb`（秒/拍）把拍换算成秒来画、来拖。
        #   `spb` 由制谱器按谱面当前的 BPM 灌进来。
        self.spb = 0.5                    # 秒/拍（0.5 = BPM 120）
        # ★ 每秒多少像素 ★
        #   原来 128：配上"至少 3 分钟"的长度就是 **23158 px 宽的控件**，
        #   每次写入都要重排一遍，慢到把"按下的时刻"都拖偏了
        #   （实测连按三下的间隔被拉成 0.5 秒）。降到 64 之后
        #   3 分钟 = 11520 px，方块还有 ~26 px 够写字。
        self.px_per_sec = 64.0
        self.selected: EdNote | None = None
        self.selected_pitch: str | None = None
        # ★ Shift 逐个多选的块 ★
        #   用户：「按住 shift 点按音频可以多选，再按取消选择」——
        #   框选只能圈**一段连续**的，而这个能挑**任意几个**：
        #   点一下进名单、**再点同一个就出来**。
        #   名单里的块可以一起拖走 / 一起删 / 一起左右微调。
        #   用 list 存（不是 set）：`EdNote` 每次改文本都会重建，
        #   这里一律靠 `is`（身份）比对，列表最直白。
        self.multi: list[EdNote] = []
        self._shift_pick: EdNote | None = None    # 按下时记的候选，松手没拖才切换
        self._multi_start: list[tuple[EdNote, float]] = []   # 整组拖动前的起点
        self.playhead = 0.0

        # ★ 拖动要不要吸附到 1/4 拍网格 ★
        #   默认 **不吸附** —— 用户的原话是「跟制作视频的时间轴一样」：
        #   拖到哪儿就是哪儿。以前这里无条件是
        #   `round((start + dx/ppb) / SNAP) * SNAP`，于是拖动是"跳格"的，
        #   想停在两个格子中间根本做不到，看起来就像"总被吸到某个整齐的位置"。
        #   制谱器上有个勾选框能把它打开（真要按拍子对齐时用）。
        self.snap_on = False

        # ★ 重叠的方块要不要自动错开轨道 ★
        #   用户：「如果有重叠就放到其他轨道上」。
        #   方块是等宽的（0.8 拍），所以"挨得近"就会在视觉上叠住 ——
        #   这个开关一开，时间上重叠的方块会自动落到不同轨道上。
        #   手动拖过轨道的方块（`lane_fixed`）不会被自动挪走。
        self.auto_lane_on = True

        # 选区（拍）
        self.sel_start: float | None = None
        self.sel_end: float | None = None
        self._band_anchor: float | None = None
        self._band_moved = False

        # 鼠标正在干什么：None / 'head_wait' 红线按下还没定是拖还是长按
        #                / 'head' 拖红线 / 'note' 挪方块（左右=改时间 上下=换轨）
        #                / 'band' 框选
        self._mode: str | None = None
        self._mouse_held = False        # 左键按在时间轴上（播放器让位，见 mouse_held）
        self._drag_x0 = 0.0
        self._drag_start0 = 0.0
        self._drag_moved = False
        # ★ 红线上的「长按」判定 ★
        #   红线既要能拖走、又要能长按框选，只能靠「按了多久」区分：
        #   按下时先不定，动得早就是拖红线，按住不动 HOLD_MS 就转框选。
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold)
        self._sa = None              # 外层 QScrollArea（惰性缓存）
        self._sa_looked = False
        self._last_w = 0             # 上次设过的最小宽度（别重复设，很贵）
        self._hover_lane = None      # 鼠标悬在哪条轨道上（轨道头跟着亮）
        # ★ 轨道数（实例属性，不是模块常量）★
        #   用户：「这里面新增一个按钮开启 12 轨，正常 6 轨」。
        #   默认 `LANES`（6），由 `set_lanes()` 切成 12。
        #   下面所有算式一律读 `self.lanes` —— 一个都不许再读 `LANES`，
        #   不然切了之后会"画 12 条、点只认 6 条"这种半截状态。
        self.lanes = LANES
        # ★ Shift + 长按 = "多选方块"（不制造选区）★
        #   `_hold_shift` 记按下那一刻的 Shift；`_band_cur` 是矩形另一头。
        self._hold_shift = False
        self._band_cur: float | None = None

        self.setMouseTracking(True)
        self.setMinimumHeight(RULER_H + self.lanes * ROW_H + 8)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------- 数据 ----------------

    def set_model(self, model: EditModel | None):
        self.model = model
        self.selected = None
        self.selected_pitch = None
        self._reset_multi()
        self.playhead = 0.0
        self.clear_selection()
        self.auto_lanes_now()
        self._update_size()
        self.update()

    def set_model_keep_head(self, model: EditModel | None, playhead: float):
        """换模型但**保留播放头**（打谱时用，不然每敲一下都被踢回开头）。"""
        self.model = model
        self.selected = None
        self.selected_pitch = None
        # ★ 模型换了 → 多选名单作废 ★
        #   `EdNote` 每次改文本都是**重建**出来的新对象，旧名单里那些
        #   一个 `is` 都对不上 —— 留着只会让块"删不掉、拖不动"。
        self._reset_multi()
        self.auto_lanes_now()
        self.set_playhead(playhead)
        self._update_size()
        self.update()

    def set_playhead(self, beat: float):
        # ★ 这就是「节拍器一会没打就停了」的真凶 ★
        #   原来上限卡在 `model.total_beats`（= 最后一个音的末尾）：
        #   播放头走到那儿就再也上不去了 —— 时钟还在走、每秒都来设一次，
        #   但每次都被夹回同一个值，看起来就是**停在原地**。
        #   ★ 换成"时间轴铺的总长"（至少 `MIN_SPAN_SEC`）★
        #   注意别用 `self.total_sec` 之外的东西：它内部就是
        #   `total_beats * spb`，同一个换算。
        limit = max(self.total_sec, MIN_SPAN_SEC) / max(1e-9, self.spb)
        beat = max(0.0, min(beat, limit))
        self.playhead = beat
        self.update()

    @property
    def mouse_held(self) -> bool:
        """你手上的左键是不是正按在时间轴上。

        ★ 为什么需要这个 ★
          播放器每 12 ms 报一次"现在到第几拍"，`_on_player_tick` 收到就
          设播放头。于是你**一按下**（拖红线 / 点空白处定位），
          手还没抬起来，播放头就被播放器抢回去 ——
          用户报的「把红线移到后面他就从头开始来」「红线拖不动」
          就是这儿来的。手指按住期间该由你说了算，
          松手时 `playhead_dropped` 再让播放器从你放下的地方接着走。

        ★ 自愈 ★
          万一 release 事件丢了（在窗口外面松的手），
          别把播放头**永久冻住** —— 顺手看一眼真实按键状态。
        """
        if not getattr(self, '_mouse_held', False):
            return False
        try:
            from PyQt6.QtWidgets import QApplication
            if not (QApplication.mouseButtons()
                    & Qt.MouseButton.LeftButton):
                self._mouse_held = False
                return False
        except Exception:
            pass
        return True

    def _update_size(self):
        # ★ 至少给 `MIN_SPAN_SEC` 那么长 ★
        #   节拍器要一直走，就得有地方让它走到底 —— 只按曲子长度算宽度的话，
        #   走到"最后一个音的末尾"就到头了，看起来像"停下来了"。
        span = max(self.total_sec, MIN_SPAN_SEC)
        w = HEADER_W + int(span * self.px_per_sec) + 60
        w = max(400, w)
        # ★ 宽度没变就别再设一遍 ★
        #   `setMinimumWidth()` 会让外层 QScrollArea 整个重排；
        #   每写一个音都调一次的话，光这一步就要几百毫秒 ——
        #   实测把"按下的时刻"都拖偏了（连按三下的间隔被拉成 0.5 秒）。
        if w != self._last_w:
            self._last_w = w
            self.setMinimumWidth(w)

    # ---------------- 选区 ----------------

    def selection_beats(self) -> tuple[float, float] | None:
        """返回 (起, 止) 拍；没有有效选区时返回 None。"""
        if self.sel_start is None or self.sel_end is None:
            return None
        if abs(self.sel_end - self.sel_start) < 1e-6:
            return None
        return (min(self.sel_start, self.sel_end),
                max(self.sel_start, self.sel_end))

    def has_selection(self) -> bool:
        return self.selection_beats() is not None

    def select_all(self):
        if self.model and self.model.notes:
            self.sel_start, self.sel_end = 0.0, self.model.total_beats
            self.update()
            self.selection_changed.emit()

    def clear_selection(self):
        # ★ 这里**不许**碰 `_band_anchor` ★
        #   它是"这一趟拖拽的落点"，跟"选区"不是一回事。
        #   原来顺手清掉了，于是这一串就出事：
        #       press    : _band_anchor = 你点到的那个时间      ← 记锚点
        #                  紧接着（同一个函数里）clear_selection()
        #                  _band_anchor = None                  ← 被吃掉
        #       release  : anchor = self._band_anchor or 0.0 → 0.0
        #                  set_playhead(0.0)                    ← 红线弹回开头
        #   用户报的「点时间轴的时候红线可能会弹回到开头」就是它。
        #   拖出距离的那条路不走这个分支，所以只有"纯点一下"中招
        #   —— 这也正好解释了为什么是"可能"会。
        self.sel_start = self.sel_end = None
        self.update()
        self.selection_changed.emit()

    # ---------------- Shift 逐个多选 ----------------
    #
    #  用户：「按住 shift 点按音频可以多选，再按取消选择」。
    #
    #  框选（`sel_start/sel_end`）只能圈**一段连续**的；
    #  这个名单是**任意几个**，落点精确 —— 隔得老远的块也能一个个挑。
    #
    #  `EdNote` 一律用 `is` 比对：模型每次改文本都会**重建**对象，
    #  用 `==` 迟早出岔子（现在默认就是身份比较，但写显式一点，
    #  免得以后有人给它加了 `__eq__`，这里就悄悄串味了）。

    def _picking(self, note) -> bool:
        """这个块在不在"Shift 长按框选"当下罩住的范围内。

        只用于**拖动中的预览** —— 松手之后它们就进 `self.multi` 了
        （那条路走 `in_multi`）。

        ★ 为什么不复用普通框选的范围 ★
          用户：「按住 shift 长按的时候所有框选的按键才亮边，
          直接框选的不需要亮边」。
          普通框选（空白处拖）划的是"要删掉的那一段"—— 把范围内的块
          也点亮，看着像"这些被选中了"，可一按删除它们**全没了**，
          两种意思差得远。所以亮边的资格只给 Shift 那一路。
        """
        if self._mode != 'multi_band':
            return False
        a, b = self._band_anchor, self._band_cur
        if a is None or b is None:
            return False
        lo, hi = (a, b) if a <= b else (b, a)
        return note.start < hi - 1e-9 and note.end > lo + 1e-9

    def in_multi(self, note) -> bool:
        """这个块在不在 Shift 多选名单里。"""
        return any(n is note for n in self.multi)

    def toggle_multi(self, note) -> bool:
        """把一个块加进 / 移出名单。返回"加进去了没有"。"""
        for i, n in enumerate(self.multi):
            if n is note:
                del self.multi[i]
                return False
        self.multi.append(note)
        return True

    def _reset_multi(self):
        """名单作废（换模型时用）。不发信号 —— 调用方自己会刷界面。"""
        self.multi = []
        self._shift_pick = None
        self._multi_start = []

    def clear_multi(self):
        """清空多选名单（「取消选区」按钮 / Esc 用）。"""
        if not self.multi:
            return
        self._reset_multi()
        self.multi_changed.emit(0)
        self.update()

    def clear_all_picks(self):
        """把三种"选中"**一次清干净**：框选、Shift 多选名单、单个选中。

        ★ 为什么要合成一个 ★
          用户报「多选之后按空白处不会取消多选」的时候，
          那个位置只调了 `clear_selection()` —— 它清的是浅蓝那条**框选**，
          而 Shift 一个个挑出来的 `multi` 名单压根没人管。

          根子是"取消"这件事原来散在四个地方各写各的（点空白 /
          点标尺 / 点红线 / Esc），漏一个就出这种"清不干净"的 bug。
          现在四处都调这一个函数，想漏也漏不掉了。

        （`update()` 放在这儿：清完就得重画，不然那几个块还亮着边。）
        """
        self.clear_selection()
        self.clear_multi()
        self.selected = None
        self.selected_pitch = None
        self.update()

    def delete_multi(self) -> int:
        """把多选名单里的块**一次性全删掉**。返回删了几个。"""
        if not self.multi or self.model is None:
            return 0
        self.undo_requested.emit()
        n = 0
        for note in list(self.multi):
            idx = self.model.index_of(note)
            if idx >= 0:
                self.model.remove_note(idx)
                n += 1
        self._reset_multi()
        self.selected = None
        self.selected_pitch = None
        self.multi_changed.emit(0)
        self._after_edit()
        return n

    def _nudge_multi(self, d: float):
        """把整组一起左右挪 d 拍（键盘微调用）。

        ★ 整组**统一**位移、一起被 0 秒卡住 ★
          逐个夹取（`max(0, start+d)`）会把组内间距破坏掉 ——
          最左边那个撞到 0 秒停下、后面的还在动，整组就被拉散了。
        """
        if not self.multi:
            return
        if d < 0:
            d = max(d, -min(n.start for n in self.multi))
        for n in self.multi:
            self.model.move_note_free(n, n.start + d)

    def set_selection(self, a: float, b: float):
        if self.model:
            a = max(0.0, min(a, self.model.total_beats))
            b = max(0.0, min(b, self.model.total_beats))
        self.sel_start, self.sel_end = a, b
        self.update()
        self.selection_changed.emit()

    def delete_selection(self, ripple: bool = True) -> int:
        """删除选区内的块。返回删了几个。

        `ripple=True`（默认）后面的往前接上；`False` 后面的留在原地。
        两者的取舍见 `EditModel.remove_range` 的说明。
        """
        rng = self.selection_beats()
        if not rng or not self.model:
            return 0
        self.undo_requested.emit()
        n = self.model.remove_range(*rng, ripple=ripple)
        self.clear_selection()
        self.selected = None
        self.selected_pitch = None
        self._update_size()
        self.update()
        self.changed.emit()
        return n

    # ---------------- 坐标（横轴 = 秒）----------------

    @property
    def px_per_beat(self) -> float:
        """每拍多少像素 —— 内部还是拍，所以留了这个换算。"""
        return self.px_per_sec * max(1e-9, self.spb)

    @property
    def total_sec(self) -> float:
        return (self.model.total_beats * self.spb) if self.model else 0.0

    def _sec_x(self, sec: float) -> float:
        return HEADER_W + sec * self.px_per_sec

    def _x_sec(self, x: float) -> float:
        return (x - HEADER_W) / max(1e-6, self.px_per_sec)

    def _beat_x(self, beat: float) -> float:
        return self._sec_x(beat * self.spb)

    def _x_beat(self, x: float) -> float:
        return self._x_sec(x) / max(1e-9, self.spb)

    def _lane_y(self, lane: int) -> float:
        """轨道 -> y（**含顶部标尺的偏移**）。

        `lane 0` 在最下面（跟原来 PAD1 在底的直觉一致）。
        """
        lane = max(0, min(self.lanes - 1, int(lane)))
        return RULER_H + (self.lanes - 1 - lane) * ROW_H

    def _y_lane(self, y: float) -> int:
        """y -> 轨道号（落在标尺上时按最上面那条算）。"""
        lane = self.lanes - 1 - int((float(y) - RULER_H) // ROW_H)
        return max(0, min(self.lanes - 1, lane))

    def lanes_bottom(self) -> float:
        """轨道区的下边界（= 标尺高 + 所有轨道高）。"""
        return float(RULER_H + self.lanes * ROW_H)

    def set_lanes(self, n: int) -> bool:
        """切换轨道数（6 / 12）。返回"轨数真的变了没有"。

        用户：「这里面新增一个按钮开启 12 轨，正常 6 轨」。

        ★ 为什么切小的时候要挪音符 ★
          12 → 6 之后，原来摆在 7~12 轨上的方块会全部落到**轨道区外面**
          （`_lane_y` 算出来的 y 是负数），画不出来也点不到 ——
          在用户看来就是"音符丢了"。
          所以切小之前先把它们压回最后一条轨道。
          这确实改了数据，但比"看不见的音符"强得多，
          而且语义上也对：按一下 6 轨就是把多出来的层收掉。

        ★ 切大不需要动音符 ★
          `lane 0` 永远在最下面，所以 0~5 轨的方块位置原样有效；
          多出来的 6 条是空的，等着用户往上摆。

        ★ 重排交给 `auto_lanes_now()` ★
          「重叠的自动错开轨道」开着的话，它会按新轨数重新摊一遍；
          关着就一个都不动（那是用户自己的选择）。
        """
        n = max(1, min(LANES_MAX, int(n)))
        if n == self.lanes:
            return False
        if self.model is not None and n < self.lanes:
            for nt in self.model.notes:
                if getattr(nt, 'lane', 0) > n - 1:
                    nt.lane = n - 1
                    # 打上"手动摆过"的标记 —— 不然 `auto_lanes_now()`
                    # 立刻又会把它挪到别的轨道去，用户看到的是"我刚收上来的
                    # 方块又自己跑了"。
                    nt.lane_fixed = True
        self.lanes = n
        self._hover_lane = None
        self._reset_multi()
        self.setMinimumHeight(RULER_H + self.lanes * ROW_H + 8)
        self.auto_lanes_now()
        self._update_size()
        self.update()
        return True

    def _note_w(self) -> float:
        """块的宽度 —— **所有块一样宽**，不按真实时值画。"""
        return max(22.0, self.px_per_beat * NOTE_W_BEAT)

    def _note_rect(self, note: EdNote, _pitch: str = '') -> QRectF:
        y = self._lane_y(getattr(note, 'lane', 0))
        return QRectF(self._beat_x(note.start), y + 4,
                      self._note_w(), ROW_H - 8)

    def _hit(self, pos) -> tuple[EdNote, str] | None:
        """点到了哪个块 —— 返回 (块, 代表音名)，没点到返回 None。

        ★ 一个和弦块只画**一个方块**（块上写 `1&3`）★
          所以"点一下只选中一个"是天然的：屏幕上就是一个方块，
          不再是"同一列上好几个方块一起亮"。
        ★ 按**画出来的样子**判命中（固定宽度），不是按真实时值 ★
          用户看到的是那个方块，点到它就该选中它。
        """
        if not self.model or pos.x() < HEADER_W:
            return None
        lane = self._y_lane(pos.y())
        first = None
        for n in reversed(self.model.notes):
            if n.is_rest or getattr(n, 'lane', 0) != lane:
                continue
            if not (n.start - 0.02
                    <= self._x_beat(pos.x()) <= n.start + NOTE_W_BEAT + 0.02):
                continue
            if n.pitches:
                # ★ 同一个位置叠着好几个块时，Shift 名单里的那个优先 ★
                #   不然"自己人"会被后写进来的块盖住，一按就抓到别人。
                if self.in_multi(n):
                    return n, n.pitches[0]
                if first is None:
                    first = n
        if first is not None:
            return first, first.pitches[0]
        return None

    def _hit_multi_near(self, pos) -> tuple[EdNote, str] | None:
        """在 Shift 名单里找一个"鼠标附近"的块（上下一条轨道内也算）。

        ★ 为什么需要它 ★
          整组挪完之后 `auto_lanes_now()` 会重新排轨 —— 挤在一起的块被
          分到别的轨道去。你松手、想再照着**原来的位置**按一下接着微调，
          `_hit()` 抓到的却是被挤过来的**另一个块**，于是：
              · 那个块被选中（白框亮起来）
              · 这一拖只动它一个，整组纹丝不动
          用户报的「多选的时候推动到其他方块会选中」就是这个。

          所以名单非空时，先在名单里找"落点附近"的块，找到了就换用它 ——
          你已经挑出来的那几个，优先级本来就该高于**恰好压在上面的**别人。
        """
        if not self.multi or self.model is None:
            return None
        lane = self._y_lane(pos.y())
        best = None
        best_d = None
        for n in self.multi:
            if not n.pitches or self.model.index_of(n) < 0:
                continue
            r = self._note_rect(n)
            # 横向必须压住（左右各放宽一点点）
            if not (r.left() - 4.0 <= pos.x() <= r.right() + 4.0):
                continue
            # 纵向：同一条轨道最优，±1 条也算（换轨之后就差这一格）
            d = abs(getattr(n, 'lane', 0) - lane)
            if d > 1:
                continue
            if best is None or d < best_d:
                best, best_d = n, d
        if best is None:
            return None
        return best, best.pitches[0]

    # ---------------- 交互 ----------------

    def _on_hold(self):
        """红线按住不动够久了 —— 转成框选；**按住 Shift 时转成"多选方块"**。

        起点就是红线所在的那一拍：「长按红线再往右拖」正好选出
        从红线到你松手之间那一段，这是最常用的用法。

        ★ Shift 那一路**不制造选区** ★
          用户：「按住 shift 的时候长按多选改成多选方块，不制造选区」。
          理由跟别的软件一致：Shift 是"把范围内的东西**加进**已选集合"，
          不是"划定一个范围"。框选（浅蓝那一条）的用途是"我要反复听
          这一段"，两者目的不同，不该共用一个手势。
        """
        if self._mode != 'head_wait':
            return
        self._band_moved = False
        # 两个点都从红线起：`_band_anchor` 是起点，`_band_cur` 是拖动中的另一头
        self._band_anchor = self.playhead
        self._band_cur = self.playhead
        self._mode = 'multi_band' if self._hold_shift else 'band'
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def mousePressEvent(self, event):
        if not self.model:
            super().mousePressEvent(event)
            return
        pos = event.position()
        btn = event.button()

        # --- 右键：把和弦里的这个音拿掉（单音块就是整块删）---
        if btn == Qt.MouseButton.RightButton:
            hit = self._hit(pos)
            if hit is not None:
                note, pitch = hit
                self.undo_requested.emit()
                self.model.remove_pitch(note, pitch)
                self.selected = None
                self.selected_pitch = None
                self._after_edit()
            event.accept()
            return

        if btn != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        self._drag_moved = False
        # ★ 这一趟拖动有没有记过撤销点 ★
        #   拖动是**连续**的：每一帧模型都在变。要是每帧都 push 一次，
        #   撤销栈立刻被中间态塞满 —— 按一次 Ctrl+Z 只退一帧的位移。
        #   所以整趟只记**一次**，而且记在第一次改动之前，
        #   存的正好是"手按下去之前"的那份谱面。
        self._undo_pushed = False
        self._mouse_held = True         # 松开之前播放器不许改播放头

        # ★ 顶部标尺 = 播放头专用区 ★（PR 的分区）
        #   在标尺上按下就是"把红线挪到这儿、然后拖着走"——
        #   不需要"长按多久才算框选"那种猜测，这里**不可能是框选**。
        #   这也是「点时间轴红线弹回开头」那类误会的根治办法：
        #   想挪红线就点标尺，想在轨道里做事就在轨道里点，两者不打架。
        if pos.y() < RULER_H and pos.x() >= HEADER_W:
            self._mode = 'head'
            self._drag_x0 = pos.x()
            self._drag_moved = False
            # ★ 标尺上按下也算"点空白"★
            #   它就是一条空带，用户点它的意思跟点轨道空白一样：
            #   定位 + 把选中的东西放下。原来这里不清，于是"点空白
            #   取消不了多选"在某些位置依然复现 —— 用户报的那条
            #   之所以看着没修好，就是漏了这几条边路。
            self.clear_all_picks()
            self.set_playhead(max(0.0, self._x_beat(pos.x())))
            self.playhead_moved.emit(self.playhead)
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.setFocus()
            event.accept()
            return

        # --- 红线：**短拖 = 挪红线，长按 = 从这儿开始框选** ---
        #     按下时先不定死，交给 `_hold_timer` 和 mouseMoveEvent 去分。
        #     按下时先不定死，交给 `_hold_timer` 和 mouseMoveEvent 去分。
        if (pos.x() >= HEADER_W
                and abs(pos.x() - self._beat_x(self.playhead)) <= HEAD_GRAB):
            self._mode = 'head_wait'
            self._band_anchor = None
            self._band_cur = None
            self._band_moved = False
            self._drag_moved = False
            self._drag_x0 = pos.x()
            # ★ 记下按下这一刻按没按 Shift ★
            #   松手前 `_on_hold` 用它分流：按住 Shift = 矩形多选方块，
            #   不按 = 老样子（长按转框选）。
            self._hold_shift = bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._hold_timer.start(HOLD_MS)
            event.accept()
            return

        # ★ 选区里**不再**劫持按下事件 ★
        #   这里原来有一段：只要落点在选区内，这一趟就变成"整段平移"
        #   （`band_move`）。用户后来要的是「把选框的功能局限于选区循环
        #   播放」—— 框选只负责圈出循环的那一段，不该顺手把鼠标操作也
        #   接管掉：想点选某个块、想拖它走、想在空白处定位播放头，
        #   只要落点恰好在选区内，全都会变成"整段一起挪"。
        #   真要一起挪几个块，用 Shift 逐个挑（`multi_move`）——
        #   那条路是**明确**的，不会抢普通点击。
        hit = self._hit(pos)
        # ★ 名单非空 + 抓到的不是自己人 → 看看落点附近有没有自己人 ★
        #   整组挪完被自动排轨挤散之后，照原位置按下去会抓到别人
        #   （详见 `_hit_multi_near`）。只在"确实点到了某个块"时才换 ——
        #   点在空白仍然是定位播放头。
        if hit is not None and self.multi and not self.in_multi(hit[0]):
            near = self._hit_multi_near(pos)
            if near is not None:
                hit = near
        if hit is not None:
            note, pitch = hit
            alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            # ★ Shift + 单击 = 把这个块加进 / 移出多选名单 ★
            #   按下时先只"记候选"，等松手确认**没拖动**才真的切换 ——
            #   不然 Shift 想细调一下位置，也会顺手把选择改掉。
            if shift and not alt:
                self._mode = 'shift_pick'
                self._shift_pick = note
                self._drag_x0 = pos.x()
                self._drag_moved = False
                self.setFocus()
                event.accept()
                return

            # Alt + 拖 = 先把这个音从和弦里拆出来，再单独挪它
            if alt and len(note.pitches) > 1:
                self.undo_requested.emit()
                out = self.model.split_pitch_out(note, pitch)
                if out is not None:
                    note = out
            self.selected = note
            self.selected_pitch = None      # 块是单位 —— 整块亮
            self._drag_x0 = pos.x()
            self._drag_start0 = note.start
            # ★ 拖多选名单里的任意一块 = 整组一起挪 ★
            #   挑了好几个块，多半就是为了让它们一起动 ——
            #   不然还得一个个拖，多选就没意义了。
            if self.in_multi(note):
                self._mode = 'multi_move'
                self._multi_start = [(n, n.start) for n in self.multi]
            else:
                self._mode = 'note'         # ★ 不再有 'stretch'（不用拉长）★
            self.setFocus()
            for p in note.pitches:
                self.note_clicked.emit(p)
            self.update()
            event.accept()
            return

        # --- 空白：按下就**立刻**把播放头挪过来，拖出去才升级成框选 ---
        #     （以前是等松手才挪，手一抖超过阈值就变成框选、红线不动，
        #       体感就是"点了一下没反应"）
        beat = max(0.0, self._x_beat(pos.x()))
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        # ★ 不按 Shift 点空白 = 把之前选的东西**全**撤掉 ★
        #   用户报的「多选之后按空白处不会取消多选」就是这个：
        #   这里原来只 `clear_selection()` —— 它清的是浅蓝那条**框选**，
        #   而 Shift 一个个挑出来的 `multi` 名单压根没人管。
        #   于是点完空白那几个块还亮着边，接着按一下 Delete 照样全删掉。
        #
        #   「框选」和「多选名单」是两套东西（一套管"反复听这一段"，
        #   一套管"这几个块一起动"），但**取消**的时候用户想的是
        #   同一件事：我点空白了，就都没有了。所以一起清 ——
        #   跟 Esc 那条路（`keyPressEvent`）保持一致。
        if not shift:
            self.clear_all_picks()
        # ★ 先清选区、**再**记锚点 ★
        #   `clear_selection()` 以前会顺手把 `_band_anchor` 清成 None，
        #   写在它后面就等于刚记的锚点被自己吃掉。现在它不碰锚点了，
        #   但这个顺序仍然保留 —— 免得以后又踩回去。
        self._band_anchor = beat
        # ★ `_band_cur` 也得在这里起个头 ★
        #   松手时 `multi_band` 那条路要读它。不置的话，没拖动的那一次
        #   读到的是**上一次**拖拽留下的陈旧值 —— 框出一个莫名其妙的范围。
        self._band_cur = beat
        self._band_moved = False
        # ★ Shift + 在空白处拖 = 框住谁就把谁加进多选名单 ★
        #   用户：「按 shift 长按选框，选区内的不会被多选」。
        #   这里原来**写死成 `'band'`**，Shift 只被用来"跳过清选区" ——
        #   于是除了"贴着红线长按"那一条窄路（`_on_hold`），
        #   Shift 框选根本不产生多选。现在两条路都通向 `multi_band`。
        self._mode = 'multi_band' if shift else 'band'
        self.set_playhead(max(0.0, beat))
        self.playhead_moved.emit(self.playhead)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.model:
            super().mouseMoveEvent(event)
            return
        pos = event.position()

        # --- 红线：还没定是「拖」还是「长按」---
        if self._mode == 'head_wait':
            if abs(pos.x() - self._drag_x0) > BAND_MIN:
                # 动得早 → 是拖红线
                self._hold_timer.stop()
                self._mode = 'head'
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                event.accept()
                return

        # --- Shift 单击的候选：动出去一点就不算"单击"了 ---
        #   那就不切换多选，当这一下没按过（也不拖动，免得误挪）。
        if self._mode == 'shift_pick':
            if abs(pos.x() - self._drag_x0) > 4:
                self._drag_moved = True
            event.accept()
            return

        # --- 拖红线 ---
        if self._mode == 'head':
            if abs(pos.x() - self._drag_x0) > 3:
                self._drag_moved = True
            self.set_playhead(max(0.0, self._x_beat(pos.x())))
            self.playhead_moved.emit(self.playhead)
            self._auto_scroll(pos)
            event.accept()
            return

        # --- 框选 ---
        if self._mode == 'band':
            cur = max(0.0, self._x_beat(pos.x()))
            a0 = self._band_anchor
            if a0 is None:
                # 锚点不该丢；真丢了就拿播放头顶上，别拿 0.0（那是"开头"）
                a0 = self.playhead
                self._band_anchor = a0
            if abs(cur - a0) * self.px_per_beat > BAND_MIN:
                self._band_moved = True
            if self._band_moved:
                self.sel_start, self.sel_end = a0, cur
                self.update()
                self.selection_changed.emit()
            self._auto_scroll(pos)
            event.accept()
            return

        # --- ★ Shift + 长按拖出来的"多选方块"★ ---
        #   全程只画预览，**不碰** `sel_start/sel_end`（不制造选区）；
        #   松手时才把框住的方块加进名单（见 mouseReleaseEvent）。
        if self._mode == 'multi_band':
            cur = max(0.0, self._x_beat(pos.x()))
            self._band_cur = cur
            a0 = self._band_anchor
            if a0 is not None and abs(cur - a0) * self.px_per_beat > BAND_MIN:
                self._band_moved = True
            self.update()
            self._auto_scroll(pos)
            event.accept()
            return

        # --- ★ 整组挪：拖多选名单里的块，整组一起走 ★ ---
        if self._mode == 'multi_move' and self._multi_start:
            dx = pos.x() - self._drag_x0
            if abs(dx) > 3:
                self._drag_moved = True
            ppb = max(1e-6, self.px_per_beat)
            want = dx / ppb
            # ★ 整组**统一**位移、一起被 0 秒卡住 ★
            #   逐个夹取会把组内间距拉散 —— 最左边那个撞到 0 停下，
            #   后面的还在动，整组就散架了。
            lo = -min(s0 for _n, s0 in self._multi_start)
            if want < lo:
                want = lo
            if not self._undo_pushed:
                self.undo_requested.emit()
                self._undo_pushed = True
            moved = False
            for n, s0 in self._multi_start:
                if abs((s0 + want) - n.start) > 1e-9:
                    self.model.move_note_free(n, s0 + want)
                    moved = True
            if moved:
                self._after_edit()
            self._auto_scroll(pos)
            event.accept()
            return

        # --- 挪音符：左右 = 改时间，上下 = 换轨道 ---
        #     ★ 没有"拉时值"了 ★ 用户：「不用拉长，每个声音长度是一样的」。
        #       时值仍然存在（决定演奏间隔），但不再用手拖，也不按比例画。
        if self._mode == 'note' and self.selected is not None:
            dx = pos.x() - self._drag_x0
            if abs(dx) > 3:
                self._drag_moved = True
            ppb = max(1e-6, self.px_per_beat)
            raw = max(0.0, self._drag_start0 + dx / ppb)
            # ★ 吸附也按**秒**走（0.05 秒一档），不是按拍 ★
            snap_b = SNAP_SEC / max(1e-9, self.spb)
            want = round(raw / snap_b) * snap_b if self.snap_on else raw
            # ★ 只挪它自己，后面的块原地不动，而且**永远不合并** ★
            #   走 `move_note_free` 而不是 `move_to_beat`：后者是间距模型，
            #   挪完会把后面整排一起带着跑（用户：「拖动前面的会影响后面的」）。
            #   撞上了也不并成和弦（用户：「不需要合并的」）——
            #   挤不开就由 `auto_lanes` 排到别的轨道去。
            # ★ 拖方块的撤销点 ★
            #   和整段平移同理：不记的话拖完就撤不回来了
            #   （用户报的「有的改动无法撤回」）。整趟只记一次，
            #   位置就在第一次真正挪动之前。
            if not self._undo_pushed:
                self.undo_requested.emit()
                self._undo_pushed = True
            got = self.model.move_note_free(self.selected, want)
            if got is None:
                pass
            else:
                # ★ 上下拖 = 换轨道 ★
                #   轨道只是排版用的"层"，跟音高、跟发声都无关 ——
                #   用户：「上下拖只是换层，避免挤在一起」。
                #   所以它**不进谱面文本**，改了只要重画就行。
                #   手动摆过的方块打上 `lane_fixed`，自动排布时不会再动它
                #   （不然 `_after_edit()` 里的 `auto_lanes_now()` 立刻把它挪回去，
                #     表现就是"上下拖了没反应"）。
                lane_want = self._y_lane(pos.y())
                if lane_want != getattr(self.selected, 'lane', 0):
                    self.selected.lane = lane_want
                    self.selected.lane_fixed = True
                self._after_edit()
            event.accept()
            return

        # --- 没按键：换光标 + 更新轨道头的悬停高亮 ---
        lane = None
        if pos.x() >= HEADER_W and pos.y() < self.lanes_bottom():
            lane = self._y_lane(pos.y())
        if lane != self._hover_lane:
            self._hover_lane = lane
            self.update()
        on_ruler = pos.y() < RULER_H
        near = (pos.x() >= HEADER_W
                and abs(pos.x() - self._beat_x(self.playhead)) <= HEAD_GRAB)
        self.setCursor(Qt.CursorShape.SizeHorCursor
                       if (on_ruler or near) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """鼠标离开 —— 把轨道头的悬停高亮收掉。"""
        if self._hover_lane is not None:
            self._hover_lane = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        self._mouse_held = False         # 松手 —— 播放器可以接着走它的了
        mode = self._mode
        self._mode = None
        self._hold_timer.stop()          # 松手了就别再触发长按
        if mode is not None:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        # --- 长按还没到点就松手 = 只是**点了一下**红线 ---
        #   ★ 那也是一次"定位"，跟点空白一样该把选中撤掉 ★
        #   红线恰好停在你手底下时，这一下走的是"红线"那条路
        #   （`HEAD_GRAB` 以内都算），原来在这儿直接 return，
        #   多选名单原封不动 —— 又一条"点空白取消不掉"的边路。
        if mode == 'head_wait':
            self.clear_all_picks()
            event.accept()
            return

        # --- ★ Shift + 单击 = 切换这个块的多选状态 ★ ---
        #   按下后拖出去过就不算单击（那会儿 `_drag_moved` 已经立起来了），
        #   这一下当没按过 —— 不切换、也不挪。
        if mode == 'shift_pick':
            note = self._shift_pick
            self._shift_pick = None
            if (note is not None and not self._drag_moved
                    and self.model is not None
                    and self.model.index_of(note) >= 0):
                # ★ 先把"之前单独选中的那个"收进名单 ★
                #   用户：「已经选中一个的时候按住 shift 的时候也要选中，
                #   前面选中的那个」。
                #   不然这个流程接不上：先普通点一下选中 A（这时它只是
                #   `selected`，不在名单里），再 Shift 点 B —— 名单里就
                #   只有 B，A 反而不见了，看着像"多选没生效"。
                #   这是文件管理器那套「Shift 扩展选择」的直觉：
                #   你之前选中的那个，本来就该跟着一起进来。
                prev = self.selected
                if (prev is not None and prev is not note
                        and self.model.index_of(prev) >= 0
                        and not self.in_multi(prev)):
                    self.toggle_multi(prev)
                self.toggle_multi(note)
                self.selected = note
                self.selected_pitch = None
                self.multi_changed.emit(len(self.multi))
                self.update()
            event.accept()
            return

        # --- 整组挪松手：点一下（没拖）就把播放头落到它起点 ---
        if mode == 'multi_move':
            self._multi_start = []
            if (not self._drag_moved and self.selected is not None
                    and self.model is not None
                    and self.model.index_of(self.selected) >= 0):
                self.set_playhead(self.selected.start)
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return

        # --- ★ 结束"多选方块"：把框住的加进名单 ★ ---
        #   注意这里**只加不减** —— 拖动途中经过的方块加进去就留着，
        #   想取消某一个，单独 Shift 点它一下即可（那是 `shift_pick`）。
        if mode == 'multi_band':
            a = self._band_anchor
            b = self._band_cur
            moved = self._band_moved
            self._band_anchor = None
            self._band_cur = None
            self._band_moved = False
            # ★ 没拖出距离的，名单不动 ★
            #   "Shift 点一下空白"就是这个情况：范围退化成一个点，
            #   照下面那个判据（`note.start < hi` 且 `note.end > lo`）
            #   会正好把"跨过这一点"的那个块**莫名其妙地选进去**。
            if (moved and a is not None and b is not None
                    and self.model is not None):
                lo, hi = (a, b) if a <= b else (b, a)
                added = 0
                for note in self.model.notes:
                    if note.is_rest or not note.pitches:
                        continue
                    if note.start < hi - 1e-9 and note.end > lo + 1e-9:
                        if not self.in_multi(note):
                            self.toggle_multi(note)
                            added += 1
                if added:
                    self.multi_changed.emit(len(self.multi))
            self.update()
            event.accept()
            return

        # --- 结束框选 ---
        if mode == 'band':
            was_moved = self._band_moved
            # ★ `or 0.0` 是个陷阱 ★
            #   锚点丢了的时候该"播放头原地不动"，而不是"挪到 0 秒" ——
            #   0.0 偏偏就是"开头"，于是任何一次锚点丢失都表现成
            #   「红线弹回开头」。宁可退回播放头自己。
            anchor = self._band_anchor
            if anchor is None:
                anchor = self.playhead
            self._band_anchor = None
            self._band_moved = False
            if not was_moved:
                # 没拖出距离 = 纯点一下：播放头已经按下时挪过去了，
                # 这里只再对齐一次（防止按下的那一刻被别的逻辑改掉）
                self.set_playhead(max(0.0, anchor))
                self.playhead_moved.emit(self.playhead)
                # ★ 正在播的时候必须补这一发 ★
                #   不发的话播放器还按老位置往下 tick，下一帧就把红线
                #   拽回去了 —— 这就是「点一下定位，红线又弹回来」的根源。
                self.playhead_dropped.emit(self.playhead)
            event.accept()
            return

        # --- 结束挪音符 / 拉时值 ---
        if mode == 'note':
            if (not self._drag_moved and self.selected is not None
                    and self.model is not None
                    and self.model.index_of(self.selected) >= 0):
                self.set_playhead(self.selected.start)
                self.playhead_moved.emit(self.playhead)
            event.accept()
            return

        # --- 红线松手：正在播的话就从新位置接着播 ---
        if mode == 'head':
            if self._drag_moved:
                self.playhead_dropped.emit(self.playhead)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    # ---------------- 小工具 ----------------

    def auto_lanes_now(self) -> int:
        """让重叠的方块自动错开轨道（手动拖过的不动）。"""
        if not self.auto_lane_on or self.model is None:
            return 0
        try:
            return self.model.auto_lanes(self.lanes, NOTE_W_BEAT)
        except Exception:
            return 0

    def follow_playhead(self):
        """播放头跑出视野就把它滚进来（播放 / 记录的时候用）。

        节拍器一直往前走，不跟随的话走一会儿红线就出屏幕了 ——
        那看起来也像"停下来了"。只在快出去了才滚，不跟你手动滚动抢。
        """
        sa = self._scroll_area()
        if sa is None:
            return
        bar = sa.horizontalScrollBar()
        if bar is None:
            return
        x = self._beat_x(self.playhead)
        left = bar.value()
        span = max(60, sa.viewport().width())
        if x < left + HEADER_W + 20:
            bar.setValue(max(0, int(x - HEADER_W - 60)))
        elif x > left + span - 60:
            bar.setValue(max(0, int(x - span + 140)))

    def _after_edit(self):
        """改完模型统一收尾。"""
        self.auto_lanes_now()
        self._update_size()
        self.update()
        self.changed.emit()

    def _scroll_area(self):
        """外层的 QScrollArea（找一次就记住）。"""
        if not self._sa_looked:
            self._sa_looked = True
            from PyQt6.QtWidgets import QScrollArea
            w = self.parentWidget()
            while w is not None:
                if isinstance(w, QScrollArea):
                    self._sa = w
                    break
                w = w.parentWidget()
        return self._sa

    def _auto_scroll(self, pos):
        """拖到视野边缘就自己滚 —— 长曲子一口气拖到底不用松手。"""
        sa = self._scroll_area()
        if sa is None:
            return
        vis = self.visibleRegion().boundingRect()
        if vis.isEmpty():
            return
        bar = sa.horizontalScrollBar()
        if pos.x() > vis.right() - EDGE_ZONE:
            bar.setValue(bar.value() + SCROLL_STEP)
        elif pos.x() < vis.left() + EDGE_ZONE:
            bar.setValue(max(0, bar.value() - SCROLL_STEP))

    def mouseDoubleClickEvent(self, event):
        """双击音符 = 从它开始播。"""
        hit = self._hit(event.position())
        if hit is not None:
            note, _pitch = hit
            self.set_playhead(note.start)
            self.seek_requested.emit(note.start)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        """Ctrl + 滚轮 = 缩放时间轴。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.px_per_sec = max(24.0, min(900.0,
                                            self.px_per_sec * factor))
            self._update_size()
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if key == Qt.Key.Key_Escape:
            # Esc = 一次全清：范围选区 + Shift 多选 + 当前选中
            self.clear_all_picks()
            event.accept()
            return

        if key == Qt.Key.Key_Z and ctrl:
            # ★ 这里要的是「撤回」，不是「存档」★
            #   原来发的是 `undo_requested`，而它连到 `EditorDialog._push_undo`
            #   —— 那个只是把当前谱面文本再存一份进撤销栈。
            #   于是按 Ctrl+Z 的表现是"什么都没发生"（栈里多一条同样的记录，
            #   还被 `_push_undo` 的去重挡掉了）。用户报的
            #   「可以 ctrl+z 撤回上一步」就是这个。
            self.undo_pressed.emit()
            event.accept()
            return

        if key == Qt.Key.Key_A and ctrl:
            self.select_all()
            event.accept()
            return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # ★ Shift 多选优先 ★ —— 挑了好几个再按 Delete，
            #   要的就是"把这几个一起删掉"，而不是去管框选范围。
            if self.multi:
                self.delete_multi()
                event.accept()
                return
            if self.has_selection():
                self.delete_selection()
            elif self.model and self.selected is not None:
                self.undo_requested.emit()
                # ★ 选中的是**和弦里的某一个音** → 只拿掉它，块留着 ★
                #   以前这里一律 `remove_note`（删整个块），
                #   于是点中 `1&3` 里的 `3` 按 Delete，`1` 也一起没了 ——
                #   右键删单音（`remove_pitch`）早就支持，键盘这条路没接上。
                #   只有整块选（`selected_pitch is None`）或块里就这一个音
                #   时，才按"删整块"处理。
                p = self.selected_pitch
                if p and p in self.selected.pitches \
                        and len(self.selected.pitches) > 1:
                    self.model.remove_pitch(self.selected, p)
                    self.selected_pitch = None
                else:
                    self.model.remove_note(self.model.index_of(self.selected))
                    self.selected = None
                    self.selected_pitch = None
                self._after_edit()
            event.accept()
            return

        if self.model and (self.selected is not None or self.multi):
            # ★ 微调 = 挪这一个块；有多选名单就**整组一起挪**，
            #   和鼠标拖动走**同一条路** ★
            #   以前这里走 `set_gap_of` → `set_gap` → `_reflow`，那是
            #   **间距模型**：按一次就把后面所有块按旧 `dur` 重新铺一遍 ——
            #   用户在时间轴上对好的秒级间隔会被改成"首尾相接"。
            #   自由格式里每个音的位置都是**绝对**的（而且 `gap_of` 在
            #   自由格式下恒为 0），所以改走 `move_note_free`：
            #   只动自己、允许重叠、永远不合并（跟 548 行拖动那条一致）。
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self.undo_requested.emit()
                d = SNAP if key == Qt.Key.Key_Right else -SNAP
                if self.multi:
                    self._nudge_multi(d)          # 整组一起走
                else:
                    self.model.move_note_free(self.selected,
                                              self.selected.start + d)
            else:
                super().keyPressEvent(event)
                return
            self._after_edit()
            event.accept()
            return

        super().keyPressEvent(event)

    # ---------------- 绘制 ----------------

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bot = self.lanes_bottom()          # 轨道区下边界

        p.fillRect(0, 0, w, h, T.BG)

        # ★ 轨道**一直在**，哪怕一个音都没有 ★
        #   用户：「如果轨道上没有音频也不要不显示轨道，轨道是一直存在的，
        #   方便节拍器使用」—— 记录就是从空谱面开始的，那一瞬间要是
        #   轨道整个收起来，你就失去了时间参考、也看不出录到哪儿了。
        #   所以底纹 / 分隔线 / 网格 / 播放头**无条件画**，
        #   只有"音符"和"刻度总长"才依赖模型。
        empty = not (self.model and self.model.notes)

        # ---- 轨道底纹（交替深浅）+ 分隔线 ----
        #   ★ 鼠标悬在哪条轨道上，那条就亮一点 ★
        #   PR 里也有这个反馈；方块叠在一起时，"我现在会落到哪条轨道"
        #   全靠它给一个明确的暗示。
        p.setPen(Qt.PenStyle.NoPen)
        for lane in range(self.lanes):
            if lane == self._hover_lane:
                c = QColor(255, 255, 255, 34)
            else:
                c = QColor(255, 255, 255)
                c.setAlpha(20 if lane % 2 else 8)
            p.setBrush(QBrush(c))
            p.drawRect(QRectF(HEADER_W, self._lane_y(lane),
                              w - HEADER_W, ROW_H))
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        for k in range(self.lanes + 1):
            y = RULER_H + k * ROW_H
            p.drawLine(int(HEADER_W), int(y), int(w), int(y))
        # 轨道区以外的地方（窗口比轨道高时）抹平，别留一条色带
        if h > bot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(T.BG))
            p.drawRect(QRectF(HEADER_W, bot, w - HEADER_W, h - bot))

        # ---- 顶部时间标尺的底 + 底线 ----
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(13, 16, 24, 246)))
        p.drawRect(QRectF(HEADER_W, 0, w - HEADER_W, RULER_H))
        p.setPen(QPen(QColor(255, 255, 255, 44), 1))
        p.drawLine(int(HEADER_W), RULER_H, int(w), RULER_H)

        if empty:
            # 空谱面也给一句提示，但**不再把整个时间轴收起来**
            p.setPen(QPen(T.TEXT_DIM))
            f0 = QFont()
            f0.setPointSizeF(11)
            p.setFont(f0)
            p.drawText(QRectF(HEADER_W, RULER_H, w - HEADER_W, bot - RULER_H),
                       Qt.AlignmentFlag.AlignCenter,
                       '轨道在的 —— 写点音符，或者点「🎹 手动演奏」按一下')

        # ---- 选区高亮（贯穿标尺 + 轨道，跟 PR 的范围条一个意思）----
        rng = self.selection_beats()
        if rng:
            x1 = self._beat_x(rng[0])
            x2 = self._beat_x(rng[1])
            wsel = max(1.0, x2 - x1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(SEL_FILL))
            p.drawRect(QRectF(x1, RULER_H, wsel, bot - RULER_H))
            # 标尺里那一段更实 —— 一眼看出"范围从哪到哪"，
            # 不用拿眼睛顺着竖线往下找边界。
            p.setBrush(QBrush(QColor(130, 195, 255, 96)))
            p.drawRect(QRectF(x1, 0, wsel, RULER_H))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(SEL_EDGE, 1.2))
            p.drawRect(QRectF(x1, 0, wsel, bot))

        # ---- ★ Shift 长按拖出来的"多选方块"预览 ★ ----
        #   用**青色**（跟已经挑中的方块同一个色系）—— 一眼就知道
        #   这一下是在"往名单里加东西"，而不是"划定播放范围"。
        if (self._mode == 'multi_band' and self._band_anchor is not None
                and self._band_cur is not None):
            x1 = self._beat_x(min(self._band_anchor, self._band_cur))
            x2 = self._beat_x(max(self._band_anchor, self._band_cur))
            band = QRectF(x1, RULER_H, max(1.0, x2 - x1), bot - RULER_H)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(56, 148, 190, 52)))
            p.drawRect(band)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(130, 240, 255), 1.4))
            p.drawRect(band)

        # ---- 秒网格：刻度按缩放自动挑一档（0.1 / 0.5 / 1 / 5 秒…）----
        #   ★ 分区画 ★：轨道区里是贯通竖线，标尺里是长短刻度 + 标签。
        #   以前标签画在**最底下**，得低头去找；PR 的 ruler 在顶上，
        #   视线本来就是从左上往右下走的。
        f = QFont()
        f.setPointSizeF(8.0)
        p.setFont(f)
        step = 60.0
        for cand in (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0):
            if cand * self.px_per_sec >= 46:
                step = cand
                break
        # ★ 网格也铺满 `MIN_SPAN_SEC` ★（节拍器要有地方走、有刻度看）
        sec_total = max(self.total_sec, MIN_SPAN_SEC)
        sec = 0.0
        while sec <= sec_total + 1e-9:
            x = self._sec_x(sec)
            if x > HEADER_W - 1:
                k = sec / (step * 5)
                major = abs(k - round(k)) < 1e-6
                if major:
                    p.setPen(QPen(QColor(255, 255, 255, 64), 1.3))
                    p.drawLine(int(x), RULER_H, int(x), int(bot))
                    p.setPen(QPen(QColor(255, 255, 255, 104), 1.2))
                    p.drawLine(int(x), RULER_H - 9, int(x), RULER_H)
                    p.setPen(QPen(T.TEXT_DIM))
                    p.drawText(QRectF(x + 4, 0, 64, RULER_H),
                               Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter, _fmt_sec(sec))
                else:
                    # 次刻度：标尺里一小根，轨道区一根很淡的参考线
                    p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
                    p.drawLine(int(x), RULER_H - 5, int(x), RULER_H)
                    p.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
                    p.drawLine(int(x), RULER_H, int(x), int(bot))
            sec += step

        # ---- 休止符占位 ----
        for n in self.model.notes:
            if not n.is_rest:
                continue
            x1 = self._beat_x(n.start)
            x2 = self._beat_x(n.end)
            if x2 - x1 < 3:
                continue
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 16)))
            p.drawRect(QRectF(x1, RULER_H + 1,
                              max(2.0, x2 - x1 - 1), bot - RULER_H - 2))

        # ---- 音符：★ 一个块只画一个方块，和弦写 `1&3` ★ ----
        #   以前是一个音画一块（同一列上叠好几个），点一下整列都亮，
        #   用户说的「一次性选中一列的」就是那个。现在块就是单位 ——
        #   音高写在块上，选中也只可能是"这一个方块"。
        #   颜色仍按音高所在区取（低音区 / 中音区 / 高音区…）。
        for n in self.model.notes:
            if n.is_rest:
                continue
            r = self._note_rect(n)
            sel = (n is self.selected)
            multi = self.in_multi(n)
            # ★ 亮边的资格只给"Shift 长按框选" ★
            #   用户：「按住 shift 长按的时候所有框选的按键才亮边，
            #   直接框选的不需要亮边」。详见 `_picking`。
            #   （普通框选的范围**仍然**画那条浅蓝色带 —— 那是"删这一段"
            #     的范围提示，跟"哪个块被挑中了"是两回事。）
            picking = self._picking(n)
            p0 = n.pitches[0] if n.pitches else ''
            cell = layout.pitch_to_cell(p0)
            base = T.ZONE_COLORS[T.zone_of(cell[0])] if cell else T.TEXT
            if multi or picking:
                # ★ 挑中的 / 正在被框住的 —— 青色、亮边 ★
                #   跟"当前选中"（白色高亮）分开，一眼能看出这几个是
                #   **挑出来准备一起处理**的。拖动中就亮，不用等松手。
                p.setPen(QPen(QColor(130, 240, 255), 2.6))
                p.setBrush(QBrush(QColor(56, 148, 190)))
                txt_col = QColor(255, 255, 255)
            elif sel:
                p.setPen(QPen(QColor(255, 255, 255), 2.4))
                p.setBrush(QBrush(T.ACTIVE))
                txt_col = T.ACTIVE_TEXT
            else:
                p.setPen(QPen(base.lighter(125), 1.2))
                p.setBrush(QBrush(base))
                txt_col = QColor(18, 22, 30)
            p.drawRoundedRect(r, 6, 6)

            label = '&'.join(n.pitches)
            # ★ 和弦里的 `&` 在窄块上可以省掉 ★
            #   块宽是固定的（等宽，这是用户明确要的），`1&3&5` 六个字符
            #   塞进二十几像素必然糊成一团。可同一块里的音本来就是
            #   "一起响"，不写 `&` 也不丢信息 —— 缩成 `135` 之后字号
            #   立刻能回到 9.5，一眼就读得出来。
            if len(label) > 4:
                label = ''.join(n.pitches)
            if r.width() > 14 and label:
                # ★ 字号跟着块宽走 ★
                #   和弦块要写 `1&3&5` 这种五个字符，而块只有二十几像素宽
                #   （`NOTE_W_BEAT × px_per_beat`），固定字号必然挤成一团。
                #   按"每个字符能分到多少像素"反算，再夹在 6.5 ~ 9.5 之间。
                #   数字和 `&` 的宽度大约是字号的 0.55 倍。
                avail = max(6.0, r.width() - 7.0)
                size = avail / (0.55 * max(1, len(label)))
                f2 = QFont()
                f2.setPointSizeF(max(6.5, min(9.5, size)))
                f2.setBold(sel or multi)
                p.setFont(f2)
                p.setPen(QPen(txt_col))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)

        # ---- 播放头：贯穿标尺 + 轨道，标尺里带个把手（PR 那种）----
        px = self._beat_x(self.playhead)
        if px >= HEADER_W:
            p.setPen(QPen(HEAD_COLOR, 2.0))
            p.drawLine(int(px), RULER_H, int(px), int(bot))
            # 把手：标尺里一根圆角竖条，底下收成一个尖，指向轨道区
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(HEAD_COLOR))
            p.drawRoundedRect(QRectF(px - 4.0, 3.0, 8.0, RULER_H - 12.0),
                              3.0, 3.0)
            p.drawPolygon(QPolygonF([
                QPointF(px - 6.0, RULER_H - 11.0),
                QPointF(px + 6.0, RULER_H - 11.0),
                QPointF(px, RULER_H - 1.0)]))

        # ---- 左侧轨道头（PR 的 track header）----
        #   音高已经写在块上了，这里只标轨道号 + 一道色条。
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(18, 22, 31, 248)))
        p.drawRect(QRectF(0, RULER_H, HEADER_W, bot - RULER_H))
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawLine(int(HEADER_W), RULER_H, int(HEADER_W), int(bot))

        f3 = QFont()
        f3.setPointSizeF(9.0)
        for lane in range(self.lanes):
            y = self._lane_y(lane)
            hover = (lane == self._hover_lane)
            if hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(70, 201, 168, 30)))
                p.drawRect(QRectF(0, y, HEADER_W, ROW_H))
            # ★ 左边那道竖杠 ★ —— PR 的轨道头一进来先看到的就是这条
            #   色条，用来分辨"这是几号轨道"。悬停时变青绿。
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(70, 201, 168) if hover
                              else QColor(112, 124, 152)))
            p.drawRoundedRect(QRectF(0.0, y + 7.0, 3.0, ROW_H - 14.0),
                              1.5, 1.5)
            p.setPen(QPen(T.TEXT if hover else T.TEXT_DIM))
            p.setFont(f3)
            p.drawText(QRectF(11, y, HEADER_W - 17, ROW_H),
                       Qt.AlignmentFlag.AlignLeft
                       | Qt.AlignmentFlag.AlignVCenter, '轨 %d' % (lane + 1))

        # ---- 左上角（标尺与轨道头交叉的那个格子）----
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(22, 26, 36, 252)))
        p.drawRect(QRectF(0, 0, HEADER_W, RULER_H))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawLine(0, RULER_H, HEADER_W, RULER_H)
        p.drawLine(int(HEADER_W), 0, int(HEADER_W), RULER_H)
        f4 = QFont()
        f4.setPointSizeF(8.0)
        p.setFont(f4)
        p.setPen(QPen(T.TEXT_DIM))
        p.drawText(QRectF(0, 0, HEADER_W, RULER_H),
                   Qt.AlignmentFlag.AlignCenter, '秒')
