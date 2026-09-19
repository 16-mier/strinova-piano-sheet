# -*- coding: utf-8 -*-
"""谱面显示 —— 4×4 网格高亮式 + **向内收缩的节奏圆圈**。

GridView  跟游戏里那台琴的排列一模一样；当前该打的键深色打底，
          后面几个音按远近依次变淡（个数可调）。

★ v1.3：加了一层"向内收缩的圆圈" ★
  用户：「改一下即将播放的按键提示，**一个圆圈向内聚集，聚集到中心的
  点上就是点的时机**，这样子更明显，**点按俩次就是俩个圆圈**」。

  每个还没到点的音，在它所在的格子上画一个**只描边、不填充**的亮黄
  圆圈。半径随剩余时间线性收缩，缩到格子中心那一点的那一刻就是这个音
  该按下的时刻。同一个键连按两下 → 两个同心圆：小的先到中心（先按），
  大的后到（后按）。

  （下落式 FallView 已经在 v1.1 砍掉：实战里 4×4 网格更好认。）
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont,
                         QPainter, QPen, QPolygonF)
from PyQt6.QtWidgets import QWidget

from core import layout
from core.timeline import Timeline

from . import theme as T


# ----------------------------------------------------------------------
# ★ 圆圈的时间与尺寸参数 ★
# ----------------------------------------------------------------------

# 圆圈从"最大"缩到中心那一点要多久（秒）。
#
# ★ 为什么是 1.2 秒 ★
#   · 太短（0.6 秒）：圈几乎是从中心"蹦"出来的，根本来不及反应 ——
#     你知道"要来了"的时候它已经到点了，等于没预告。
#   · 太长（2.5 秒以上）：屏幕上会同时挂着五六个圈（`preview_count`
#     默认 5），远远近近一大片，反而分不出**最近**的那个是哪一圈，
#     而玩家真正要的恰恰是"下一个"。
#
#   1.2 秒 ≈ 120 BPM 下的两拍半。这个长度刚好罩住"接下来两三个音"
#   （一般曲子一秒钟弹 2~4 个音），既看得清先后、又不至于糊成一片。
#   实测手感：从圈出现到缩到中心，够你看一眼并抬手，不会手忙脚乱。
#
#   ★ 现在它退化成"上限 + 默认值"，不再是固定时长 ★
#     用户：「可以把那个圆圈往内缩入的速度跟音符速度匹配」
#           「同一个速度有时候太慢」。
#
#     原来这里是**一个固定值**：不管曲子多快多慢，圈永远用 1.2 秒缩完。
#     两头都不对 ——
#       · 快的地方（相邻两个音只隔 0.25 秒）：圈收得太慢，屏幕上
#         同时挂着好几个圈在慢慢缩，反而看不出"下一个是哪个"；
#       · 慢的地方：圈早早缩到中心，然后杵在那儿等半天。
#
#     现在**每个音用自己的间隔**：
#         lead = 这个音的 start_sec - 上一个音的 start_sec
#     （在 `_upcoming_timed()` 里算好，跟着 `f.timed` 一路传下去）。
#     快的地方圈收得快、慢的地方收得慢 —— 而且"圈开始缩"那一刻
#     天然就踩在**上一个音**上，"缩到中心"永远落在**这个音**上。
#
#     于是 1.2 这个数只剩两个作用：
#       · **上限** —— 慢曲子里两个音隔 3 秒，圈不该在屏幕上挂那么久；
#       · **默认值** —— 曲子第一个音没有"上一个音"，就用它。
LEAD = 1.2

# ★ 收缩时长的下限（秒）★
#   间隔比这个还短的话（180 BPM 的十六分音符只隔 0.083 秒），
#   圈就变成"闪一下"了 —— 那还不如不画：它来不及传达任何
#   "还剩多久"的意思，只会让人以为屏幕在抖。
#   0.35 秒是"还看得出它在缩"的最短时间。
LEAD_MIN = 0.35

# 圆圈最大半径 = 格子边长 × 这个系数。
#
# ★ 0.45 —— 这里有个不能再大的硬约束 ★
#   格子边长是 `cell`，圆心在格子中心，所以半径一超过 0.5 就**串到隔壁
#   格子里去了**。0.5 看着还有余量，其实不是：
#     · 当前格还要"微微放大" 5%（`inset = -cell * 0.05`），
#       放大之后半格只剩 0.525，圆再粗一点就压线了；
#     · 抗锯齿的描边宽度还得往外占 `RING_W_MAX_FRAC` 的一半。
#   0.45 留出约 5% 的余量，粗圈也不会碰到格线。
RING_MAX_FRAC = 0.45

# 描边线宽：刚出现时最细、到点前最粗（按格子边长的比例算，
# 这样浮窗拉大拉小视觉比例不变）。
RING_W_MIN_FRAC = 0.020
RING_W_MAX_FRAC = 0.060

# 描边透明度：刚出现时最淡、到点前最亮。
RING_A_MIN = 70
RING_A_MAX = 255

# 进度低于这个值时，在圆心补一个实心亮点。
#
# ★ 这个数被实测打过一次脸：原来取 0.34 ★
#   0.34 意味着**还剩 0.41 秒**（0.34 × 1.2，那会儿分母还是固定的）时，
#   中心就冒出亮点了。
#   用户看到那颗点，以为"到了"，于是报：
#   「圆圈圈到中心点的时机**有点提前了**」——
#   提前量整整 0.4 秒，难怪感觉得出来。
#
#   现在 0.12：亮点只在**最后 0.14 秒**出现。它不再负责"预告还有多久"
#   （那是圆环的活儿），只负责"就是现在"这最后一下。
RING_DOT_AT = 0.12
# 亮点半径 = 格子边长 × 这个系数。
RING_DOT_FRAC = 0.085

# ★ 「聚拢完成」比音开始晚多少秒 ★
#
#   用户实测：「圆圈圈到中心点的时机**有点提前了**」。
#
#   两个来源，都修了：
#     ① 中心亮点出现得太早（见上面 `RING_DOT_AT`）—— 那是主因，0.4 秒；
#     ② 圆环半径小到一定程度，**看起来**就是"到中心了"，
#        而这时 `left` 还剩零点几秒。这是纯视觉的，改不掉 ——
#        所以给一个**时间补偿**：让"聚拢完成"发生在音开始**之后**
#        `RING_LAG` 秒，你在圈还在收的那一刻按下去，正好。
#
#   为什么宁晚勿早：早了你要等它（手上会迟疑），晚了它还在收你就按了
#   （手上是连贯的）。节奏提示宁可"刚刚好偏晚一点点"。
#
#   觉得还提前就调大这个数，一个数管全部。
RING_LAG = 0.10

# ★ 音已经开始了，圆圈还要在中心留多久（秒）★
#
#   圆缩到中心 = "就是现在"。可 `set_time` 是 8 ms 一跳，而"刚好为 0"
#   这一帧很可能被跨过去 —— 于是"到点了"的画面一帧都不显示。
#   留一条 0.45 秒的尾巴，让中心那颗亮点**稳稳地亮一下**再消失，
#   这才是用户要的"聚集到中心的点上就是点的时机"。
#
#   为什么不是留到整个音结束：谱子里全音符能响 2 秒，那颗点就会在
#   中心杵 2 秒，看着像"卡住了"。0.45 秒够看清、又不拖泥带水。
RING_TAIL = 0.45


# ★ `_pf()` / `twins()` 在这里删掉了 ★
#
#   它们原来算的是"和它**同音高**的所有键"—— 也就是 `8` 和 `1'` 这一对，
#   让浮窗把两个键**一起**点亮。理由很硬：这两个键的采样文件逐样本完全相同
#   （`tools/cmp_twin.py` 实测：归一化后最大差 0.000000、频谱差 0.00 dB），
#   从声音里**根本不存在**"玩家敲的是哪一个"这个信息，只能二选一猜。
#
#   但那是「听歌识谱」时代的东西 —— 那条路已经整条拆掉了。
#   现在浮窗的闪烁只有一个来源：制谱器的打击垫（`control._on_pad_hit`），
#   你点的是哪个格子、程序**当场就知道**，这条路上没有任何要猜的东西。
#
#   所以按用户的要求「完全按照琴谱」走：谱面/按键写 `8` 就只闪 `8`，
#   写 `1'` 就只闪 `1'`，谁也不再牵连谁。



class SheetView(QWidget):
    """两种视图的公共部分。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline: Timeline | None = None
        self.sec = 0.0
        self.preview_count = 5
        self.show_labels = True
        self.bg_scale = 1.0              # 底板浓度（1 = 原样，0 = 全透明）
        self.hidden = False              # 临时隐身：什么都不画
        # ★ 透视贴合：已删除 ★
        #   这里原来放着 `fit_quad` / `fit_us` / `fit_vs` 三份几何，
        #   配合 `_fit_transform()` 把整块 4×4 网格按透视投到游戏里那台琴上。
        #   用户后来决定整套拿掉（「自动贴合也删掉，可以调整大小和位置
        #   就行了」），浮窗回到**固定摆放**：位置和大小由外面的
        #   `x/y/w/h` 决定，绘制就是老老实实画在自己这块画布上。
        #
        # 要不要把「当前该打的音」**一直**涂成黄色。实时跟弹时关掉 ——
        # 黄色（谱面提示）和青色（实际敲的键）混在一起会看不清；
        # 但也不能一点反馈都没有，所以关掉之后改成「换音的那一下闪一下」。
        #
        # ★ v1.3 起，这个开关还管着**收缩圆圈画不画** ★
        #   理由见 `_paint_rings()` 的注释：圆圈是亮黄的"该弹这里"，
        #   跟弹模式下那份黄色得整个让给"你按了哪个键"的反馈层。
        self.mark_current = True
        self.mark_hold = 0.18            # 闪烁持续多久（秒）
        # ★ 点格子出声（「可按」开关）★
        #   默认关。它管的不只是"要不要响应" —— 浮窗整窗带
        #   `WS_EX_TRANSPARENT`（鼠标完全穿透），事件压根到不了这里；
        #   打开它会顺手把穿透取消（见 `control._set_pad_click`）。
        self.pad_click = False

        # ★ 「训练」模式 ★
        #   用户：「跟打功能旁边再增加一个训练功能，具体就是去点按键，
        #   但是不是按照曲子顺序来是按照音符顺序来，自己点，
        #   点一个继续下一个」+「整体和示谱器一样，就是播放改成手动点」。
        #
        #   跟「跟打」的区别就一句话：
        #     跟打看**时间**（播放进度在跑，你得跟上）
        #     训练看**顺序**（谱面第 1、2、3… 个音，点对当前这个才亮下一个）
        #   也就是把"什么时候点"交给谱面顺序、"来不来得及"交给自己。
        #
        #   ★ 显示上跟谱面窗**完全一样**（用户那句"整体和示谱器一样"）★
        #     所以这里**不另写绘制**，只换数据源（见 `_train_frame()`）——
        #     喂给同一套 `_paint_cells` / `_paint_labels` / `_paint_badges`。
        #     长得一样就成了**结构上的保证**，不靠人两边对着调颜色
        #     （第一版就是各画一套，结果底色字号全是另一套，用户一眼看出不对）。
        self.train_on = False
        self.train_seq: list[str] = []   # 摊平后的音名序列（控制台塞进来）
        self.train_i = 0                 # 练到第几个（0 = 还没点）
        self.train_text = ''             # 进度那行字，如 "3 / 60"
        # 训练序列里**每个音到下一个音的间隔**（秒），控制台塞进来。
        # 它就是"标记间隔时间"的数据源 —— 见下面的 `train_gap`。
        self.train_gaps: list[float] = []

        # ★ 「标记间隔时间」★
        #   用户：「然后需要标记间隔时间的这样子直观」。
        #
        #   训练不看时间，节奏信息就丢了 —— 光知道"下一个是哪个键"，
        #   不知道"这两个之间该隔多久"，练出来的是按键顺序、不是曲子。
        #
        #   所以给每个音配一个**间隔**（到下一个音还有多久），
        #   点对之后重新起算，让**收缩圆圈**照常缩：
        #   圈缩到中心 = "按这个节奏，现在该点下一个了"。
        #   早点晚点都随你（训练本来就不强制节奏），圈只是那把尺子。
        #
        #   ★ 这个"现在"用的是 `time.monotonic()`，不是曲子的 `sec` ★
        #     训练压根没在播放，`self.sec` 是冻的；要让圈动起来，
        #     只能挂真实时间。反正它跟谱面时间没有任何关系。
        self.train_gap = 0.0             # 当前音到下一个音的间隔（秒）
        self.train_t0 = 0.0              # 当前音是什么时候点出来的
        self._train_tick_t = QTimer(self)
        self._train_tick_t.setInterval(16)      # ~60fps，圈看着才顺
        self._train_tick_t.timeout.connect(self._on_train_tick)
        self._cur_stamp = 0.0            # 上一次「当前音换人」的时刻
        self.flash: dict[str, float] = {}   # 实时跟弹：音高 -> 到期时刻
        self._flash_last: dict[str, float] = {}   # 每个键上次闪的时刻（防闪花眼）
        self.max_flash = 4                # 同时最多亮几个格子
        self._last_key = None            # 上一帧的绘制内容指纹（用于省重绘）
        # ★ 这里**不要**再设 WA_TranslucentBackground ★
        #   父窗口（OverlayWindow）已经设过了，子控件再设一次会把自己
        #   变成**原生窗口**，于是鼠标消息被它自己接走、不再冒泡到父窗口 ——
        #   表现就是「点了允许拖动却怎么都拖不动」。
        #   QWidget 默认本来就不画背景，透明效果不受影响。

    # ---- 数据接口 ----

    def set_timeline(self, tl: Timeline | None):
        self.timeline = tl
        self._last_key = None
        self.update()

    def set_time(self, sec: float):
        self.sec = sec
        self.update()

    # ---- ★ 透视贴合 ★ ----

    # ---- 工具 ----

    def _panel(self, p: QPainter):
        w, h = self.width(), self.height()
        p.setPen(QPen(self._dim(T.BG_EDGE), 2))
        p.setBrush(QBrush(self._dim(T.BG)))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 16, 16)

    def set_bg_scale(self, scale: float):
        """底板浓度 —— 只影响背景和格子，音名文字一点不受影响。"""
        self.bg_scale = max(0.0, min(1.0, float(scale)))
        self._last_key = None
        self.update()

    def set_mark_current(self, on: bool):
        """要不要**一直**涂黄「当前该打的音」（实时跟弹时关掉）。

        关掉之后当前格仍会闪一下（见 `current_blinking`），
        只是不再长期占着黄色 —— 黄、青两块叠在一起根本分不清谁是谁。

        v1.3 起它也控制收缩圆圈，理由同上：那层圆圈是黄色的。
        """
        self.mark_current = bool(on)
        self._last_key = None
        self.update()

    def current_blinking(self) -> bool:
        """当前格是不是**刚刚换过来** —— 那一下要闪。

        ★ 为什么去掉了 `mark_current` 这个前提 ★

          原来这里写的是 `if self.mark_current: return False` ——
          也就是"当前格常亮着的时候就不闪了"。

          可用户要的恰恰是两件事同时成立：

              常亮（深色打底）= 「接下来该弹这个」
              换音瞬间闪一下 = 「就是现在」

          一个是底色、一个是节拍提示，本来就不冲突。
          用户的原话：「按写好了的铺子判断，然后**正在播放的按键闪烁**」。

        ★ 这一下是**纯谱面驱动**的，所以不可能有延迟 ★

          `_cur_stamp` 在 `set_time()` 里更新，而 `set_time` 是由
          播放时钟每 8 ms 喂进来的。也就是说"闪"发生在
          **谱面时间跨过那个音的 `start_sec` 的那一刻** ——
          中间没有音频、没有识别、没有等待。
          （时钟到浮窗的实测延迟：0.0000 秒，见 `tools/diag_latency.py`。）

          相比之下，之前那版是靠**听到声音**才闪（`_on_onset` → `flash_note`），
          于是闪的动作背着整条音频链路的延迟 ——
          用户看到的「闪烁的键落后两个」就是这么来的。

        `has_flash()` 也用它来判断"还有没有东西要重绘"，所以这里必须
        如实回答"现在到底闪不闪"。
        """
        if not self._cur_stamp:
            return False
        return (time.monotonic() - self._cur_stamp) <= self.mark_hold

    def set_hidden(self, on: bool):
        """什么都不画 —— 配合 WA_TranslucentBackground 就等于完全透明。

        ★ 别用 setWindowOpacity(0) 干这事 ★
          那会去动窗口的 WS_EX_LAYERED 属性，跟"鼠标穿透"那套
          exstyle 管理打架 —— 实测勾上「允许拖动」后浮窗会变成一片白。
        """
        self.hidden = bool(on)
        self._last_key = None
        self.update()

    def _dim(self, c: QColor) -> QColor:
        """按底板浓度把颜色调淡（bg_scale = 1 时原样返回）。"""
        if self.bg_scale >= 0.999:
            return c
        out = QColor(c)
        out.setAlpha(int(round(c.alpha() * self.bg_scale)))
        return out

    # ---- 实时跟弹的高亮 ----

    def set_flash(self, pitch: str, seconds: float = 0.35,
                  min_gap: float = 0.45):
        """让某个键亮一下 —— 游戏里敲了哪个就亮哪个。

        ★ 同一个键 0.25 秒内重复触发会被忽略 ★
          采样是 0.79 秒的衰减音，识别器在它衰减的过程中可能报好几次
          （各次谐波衰减速度不同，激活会起伏），用户看到的就是
          **「按一下，浮窗闪 2~3 下」**。
          试过在识别器层面治（去抖窗口、峰值跟踪、能量上升门限…），
          但那些手段**同时也会吃掉真正的快速连奏**（同一个键 0.27 秒后
          再按一次）—— 两者在能量上是同一个现象，分不开。
          所以放到这一层：识别器保持灵敏，浮窗只负责别闪花眼。

        ★ 只闪这一个键 —— 不再"同音键一起亮" ★
          以前 `8` 和 `1'` 会**一起**闪，因为那会儿闪烁来自**麦克风识别**：
          这两个键的采样逐样本相同，从声音里判断不出敲的是哪一个，
          只能两个都亮（用户按 `8` 却看到 `1'` 在闪，就是这么来的）。

          现在闪烁由打击垫点击**直接驱动**，没有要猜的东西 ——
          所以严格按谱面/按键走：写 `8` 就闪 `8`，写 `1'` 就闪 `1'`。
        """
        if not pitch:
            return
        now = time.monotonic()
        if now - self._flash_last.get(pitch, 0.0) < min_gap:
            return                       # 同一个键刚闪过，别闪第二下
        self._flash_last[pitch] = now
        self.flash[pitch] = now + max(0.1, float(seconds))
        # ★ 同时高亮的格子数设个上限 ★
        #   弹快的时候高亮会在屏幕上叠起来，看着就像「按一下亮了一堆」。
        #   超过上限就把最早到期的几个踢掉 —— 反正它们也快灭了。
        if len(self.flash) > self.max_flash:
            for p, _e in sorted(self.flash.items(), key=lambda kv: kv[1]
                                )[:len(self.flash) - self.max_flash]:
                del self.flash[p]
        self._last_key = None
        self.update()

    def has_flash(self) -> bool:
        """还有没有"音频触发的高亮 / 换音闪光"要画。

        ★ 它**不**管收缩圆圈 ★
          `overlay._on_flash_tick` 拿它决定那个 8 ms 的定时器要不要停。
          圆圈的重绘由 `set_time()` 自己负责（播放时钟本来就在跑），
          两边各管一段，互不干扰 —— 把圆圈也算进来的话，曲子一停、
          圆圈还挂在屏幕上时，这个定时器会永远空转。
        """
        now = time.monotonic()
        for p in list(self.flash):
            if self.flash[p] <= now:
                del self.flash[p]
        # 「当前格闪一下」也算：闪完得有人来把它擦掉
        return bool(self.flash) or self.current_blinking()

    def clear_flash(self):
        self.flash.clear()
        self.update()

    def _hint(self, p: QPainter, text: str):
        p.setPen(QPen(T.TEXT_DIM))
        f = QFont()
        f.setPointSizeF(15)
        p.setFont(f)
        p.drawText(QRectF(0, 0, self.width(), self.height()),
                   Qt.AlignmentFlag.AlignCenter, text)

    def _current_group(self) -> list[tuple[list[tuple[int, int]], bool, str]]:
        """往后取 preview_count 个音符，转成 [(格子列表, 是否休止, 音名)]。

        ★ 这个三元组的形状**不许动** ★
          `cell_orders()` / `repeat_run()` 和 `tests/test_views.py`
          全靠它 unpack（`for cells, rest, name in group`）。
          要时间的话走下面那个平行的 `_upcoming_timed()`。
        """
        if not self.timeline or not self.timeline.items:
            return []
        out = []
        for item in self.timeline.upcoming(self.sec, self.preview_count):
            cells = []
            for pitch in item.chord.pitches:
                cell = layout.pitch_to_cell(pitch)
                if cell is not None:
                    cells.append(cell)
            out.append((cells, item.chord.is_rest,
                        '+'.join(item.chord.pitches)))
        return out

    def _upcoming_timed(self) -> list[tuple[
            list[tuple[int, int]], bool, str, float, float]]:
        """和 `_current_group()` 同源，但多带两样东西：
        「这个音在第几秒」和「这个音的圈该用多久缩完」。

        ★ 为什么要另开一个方法，不把时间塞进 `_current_group()` ★
          返回形状是别人依赖的契约（见 `_current_group()` 的注释），
          塞个第四项进去 → 所有 `for cells, rest, name in group` 一起炸，
          而其中一部分在测试里。与其改契约，不如**再开一条平行的**：
          形状一样、只多两个字段，谁要时间谁来拿。

        ★ 最后一个字段 `lead`：**每个音用自己的间隔** ★
          用户：「可以把那个圆圈往内缩入的速度跟音符速度匹配」
               「同一个速度有时候太慢」。
          见 `LEAD` 那段注释。算法一行：

              lead = 这个音的 start_sec - 上一个音的 start_sec

          然后夹在 `[LEAD_MIN, LEAD]` 之间：
            · 下限挡住"快到圈会闪一下"（十六分音符连击）；
            · 上限挡住"慢到圈挂半天"（长休止之后的第一个音）。

          ★ 这里自己遍历 `items`，不调 `timeline.upcoming()` ★
            因为要**索引**才能回头拿 `items[i-1]`。行为跟 `upcoming()`
            完全一致（它就是 `items[start:start+count]`），只是多一个
            `i` 可用。
        """
        if not self.timeline or not self.timeline.items:
            return []
        items = self.timeline.items
        start = max(0, self.timeline.index_at(self.sec))
        out = []
        for i in range(start, min(start + self.preview_count, len(items))):
            item = items[i]
            cells = []
            for pitch in item.chord.pitches:
                cell = layout.pitch_to_cell(pitch)
                if cell is not None:
                    cells.append(cell)
            if i > 0:
                lead = item.start_sec - items[i - 1].start_sec
                lead = max(LEAD_MIN, min(LEAD, lead))
            else:
                lead = LEAD               # 曲子第一个音：没有"上一个"可比
            out.append((cells, item.chord.is_rest,
                        '+'.join(item.chord.pitches), item.start_sec, lead))
        return out

    # ---- 收缩圆圈的"还有东西要重绘吗" ----

    def _rings_alive(self, timed=None) -> bool:
        """屏幕上还有正在收缩（或刚缩完留了条尾巴）的圆圈吗。

        `timed` 已经在手上的话传进来，省一次 `_upcoming_timed()` ——
        `set_time()` 每 8 ms 就要问一次这个问题，别白算两遍。
        """
        if self.hidden:
            return False                 # 不画 = 屏幕上什么都没有
        if timed is None:
            timed = self._upcoming_timed()
        for _cells, rest, _name, start, lead in timed:
            if rest:
                continue                 # 休止符没有格子可画
            left = start - self.sec
            # ★ 这个判据必须跟 `_paint_rings` / `_paint_countdown` 一致 ★
            #   它们现在都用统一的 `LEAD` 当"出现窗口"（见那边的注释：
            #   连按的几个圈要同时挂出来）。
            #   这里要是还用各自的 `lead` 就会**提前返回 False** ——
            #   屏幕上明明还挂着圈，重绘却停了，圈会**冻在半路上**。
            if -RING_TAIL <= left <= LEAD:
                return True
        return False


def repeat_run(group, start: int, cell) -> int:
    """从 `group[start]` 起，这个格子**连着**出现几次。

    ★ 「连按」是相邻重复，不是"总共出现几次" ★
      用户：「如果是连续点俩下在 1 下面写一个 ×2」。
      `5 5` → 要连按两下，返回 2。
      `5 3 5` → 两次之间隔着个 3，不用连按，返回 **1**（不是 2）。
      这个区别很重要：如果把"总共两次"当成"要连按"，
      玩家会在中间那个 3 上被误导着多按一下。

      和弦按"该键在不在这个音里"算 —— `5 5&3 5` 里 5 要按三下，
      中间那下虽然和 3 一起响，但 5 确实还得按。

      休止符会中断连续（那段是真的没声音）。
    """
    n = 0
    for i in range(start, len(group)):
        cells, rest, _name = group[i]
        if rest or cell not in cells:
            break
        n += 1
    return n


def cell_orders(group) -> dict[tuple[int, int], list[int]]:
    """预览里的音符 -> {格子: [它出现的**所有**次序]}。

    ★ 为什么要收全部，不能用 `setdefault` 只留第一个 ★
      用户报的：「同一个按键需要按下两次的时候显示不明显」。

      原来这里是 `order.setdefault(cs, rank)` —— 同一个格子只记住
      **最早**那次出现的序号。于是 `5 5`（同一个键连按两下）在浮窗上
      只看到一个 `1`，**完全看不出还要再按一次**。
      这台琴上"同一个键连着按"很常见（旋律里的重复音），
      看不出来就会漏掉第二下。

      现在返回的是列表：`{5 所在的格子: [0, 1]}` 表示
      "现在按一下，下一个还要按同一个键"。

    单独抽成纯函数是为了能测 —— 它原来是算在 `paintEvent` 里的，
    而"画一遍再看像素"这种验证方式又脆又难写（见 `tests/test_views.py`）。
    """
    orders: dict[tuple[int, int], list[int]] = {}
    for rank, (cells, _rest, _name) in enumerate(group):
        for cs in cells:
            orders.setdefault(cs, []).append(rank)
    return orders


@dataclass
class _Frame:
    """一帧要用到的**所有**几何量与数据。

    ★ 为什么要把这些东西打包 ★
      `paintEvent` 原来是一个 240 行的巨型方法，一半的代码都在重算
      `ox / oy / cell` 那几个坐标。拆成小函数之后，它们总得拿到同一套
      几何量 —— 要么一路当作参数传（七八个参数，签名长得没法看），
      要么塞到 `self` 上（会跟"视图状态"混在一起，下次谁读到
      `self.ox` 都不知道它是上一帧的残留还是真的配置）。
      打包成这一只**只活一帧**的小盒子，两边都不占。
    """

    w: float
    h: float
    cell: float
    # 网格外框的边长（4 格 + 3 个 `T.GAP`）。
    #   （透视贴合删掉之前，用户拖的四个角对应的正是这块区域；
    #    现在没有贴合了，它就是"网格自己那块正方形"的边长。）
    side: float
    ox: float
    oy: float
    # [(格子列表, 是否休止, 音名)] —— 形状跟 `_current_group()` 一致
    group: list[tuple[list[tuple[int, int]], bool, str]]
    # 同上，但多两个数：
    #   start_sec —— 这个音在第几秒（收缩圆圈要算"还剩多久"）
    #   lead      —— 这个音的圈该用多久缩完（= 它跟上一个音的间隔，
    #                夹在 `[LEAD_MIN, LEAD]` 之间；见 `_upcoming_timed()`）
    timed: list[tuple[list[tuple[int, int]], bool, str, float, float]]
    orders: dict[tuple[int, int], list[int]]
    blinking: bool                   # 谱面提示层整体开着吗
    hot_flash: bool                  # 「就是现在」那一下还亮着吗
    cur_cells: list[tuple[int, int]]


class GridView(SheetView):
    """4×4 网格高亮式。"""

    # ★ 用户在浮窗上点了一个格子（参数是音名）★
    #   浮窗平时是鼠标穿透的，这个信号发不出来 —— 只有「可按」打开
    #   （`pad_click = True` + 窗口取消穿透）之后才有人点得到它。
    pad_pressed = pyqtSignal(str)
    # ★ 顶部那一行也去掉了（`HEADER_H` 恒为 0）★
    #   这里原来画着「当前该打的音」一个大黄字（截图里那个 "5"，
    #   休止时是"休止 0.5 秒"）。
    #   用户：「这里不需要显示数字，直接把上面的搬到里面去就行」——
    #   那个大字跟网格里的高亮说的是同一件事（当前格本身就是深橄榄色，
    #   打下去再闪一下），属于重复；删掉之后它占的那 50 px 全给网格，
    #   格子更大。
    #   「上面的」= 曲名行，它现在紧贴在控制条下面（`OverlayWindow.lbl_song`）。
    #   这个常量留着只为 `_frame()` 里那处加法不用改，**不是**还有抬头。
    HEADER_H = 0
    # ★ 「只当琴键用」——「可按」**单独**开着的时候 ★
    #   用户连着报了两轮：
    #     「可按的时候不要显示高亮」
    #     「可按的时候高亮没有消失」
    #   第一轮我理解成了"那颗按钮自己别高亮"，改错了方向（见
    #   `DragHandle._build_bar` 里 `btn_tap` 那段）。真正说的是**这个**：
    #   开着「可按」是要自己动手弹，屏幕上还留着"该打哪个键"的
    #   亮格 + 收缩圆圈 + 序号小圆标，纯属干扰 —— 该收起来。
    #
    #   置真之后 `paintEvent` 只摆一副空键位（`_paint_blank_cells`），
    #   画出来跟"还没载入谱面"一样干净，但 16 个格子照样在，
    #   点下去照样出声。
    #
    #   ★ 「跟打」不算 ★
    #     跟打就是照着谱面打，提示必须有 —— 所以这个标志由
    #     `control._refresh_keys_only()` 拍板：**可按开着、而且跟打关着**
    #     才置真。两个都开的时候提示还在（跟打优先）。
    keys_only = False
    # ★ 底部不再留文字带（`FOOTER_H` 恒为 0）★
    #   用户：「这些没有用，只需要显眼的接下来打哪几个按键就行了」——
    #   那行「下一个：1 → 5 → 5 → 6」跟网格里的淡黄预告格说的是同一件事，
    #   删掉之后 40 px 全给网格，格子更大、更显眼。
    #   这个常量留着只为布局算式里那一处加法不用改，**不是**还有文字带。
    FOOTER_H = 0
    PAD = 12

    # ---- 重绘判断 ----

    def set_time(self, sec: float):
        """这一帧画的东西没变就不重绘 —— 但**正在收缩的圆圈一直在变**。

        ★ 为什么原来那套"指纹比对"不够用了 ★
          时钟跑到 ~120fps，网格只在「当前音换人了」那一刻才真的变，
          所以原来这里做指纹比对、绝大多数 tick 零开销。
          可 v1.3 加进来的圆圈**每一帧半径都不一样** —— 光靠指纹，
          它会在第一次画出来之后冻在屏幕上，缩到一半就不动了。
          （这就是加圆圈时最容易踩的坑：逻辑对了、画面是死的。）

          所以判据变成两条，**任意一条成立就重绘**：
            ① 内容指纹变了（换音了 → 顺便重新起算"闪一下"）
            ② 屏幕上还有没缩完的圆圈（那就得每帧跟着变）
          曲子停了、圈也走完了之后，`_rings_alive()` 变假、指纹也不变，
          于是自动回到零开销 —— 该省的还是要省下来。

        ★ 为什么只在这里判，不去动 `has_flash()` ★
          `has_flash()` 是给 `overlay._on_flash_tick` 那个 8 ms 定时器
          用的"要不要继续空转"。`set_time` 是播放时钟喂的，本来每 8 ms
          就会来一次 —— 圆圈挂在这儿最自然，也不必再多起一个定时器。
        """
        self.sec = sec
        timed = self._upcoming_timed()
        key = tuple((tuple(cells), rest, name)
                    for cells, rest, name, _t, _lead in timed)
        changed = key != self._last_key
        if changed:
            self._cur_stamp = time.monotonic()       # 换音了 → 黄色重新闪一下
            self._last_key = key
        if changed or self._rings_alive(timed):
            self.update()

    # ---- 绘制 ----

    def paintEvent(self, _ev):
        """只负责"画哪几层、什么顺序"，具体每一层各自成函数。

        ★ 分层顺序是**试出来的**，不是随手排的 ★

            格子底 → 两层整格反馈 → **收缩圆圈** → 音名文字
            → 序号角标/`×N` → 底部红框

          原先的写法是「格子（底 + 文字 + 角标）→ 闪光 → 圆圈」，
          画出来一看有两个毛病（`_ring_probe.py` 的截图里一眼可见）：

          ① **圆圈把音名划花了**
             圆的半径从 0.45 格缩到 0，中间必然扫过格子正中的音名。
             截图里 `5 5` 连按那两个同心圆正好叠在 "5" 字上，
             字被两道黄线加一个亮点切得看不清。而"对着游戏找键"
             全靠这行音名和下面的 "PAD N" —— 动效不能吃掉信息。
             所以把圆圈挪到**文字下面**：圈照样看得见（它是细线，
             从字旁边绕得过去），字始终是清清爽爽的一整块。

          ② **角标被圆圈穿过**
             圆最大时半径 0.45 格，而左上角那颗深色序号圈距格子中心
             只有 0.38 格 —— 几何上**必然**相交。所以角标排在圆圈
             之后画，让它压住圈。序号是"第几个弹"的信息，更重要。

          两层整格反馈（`hot_flash` / `flash`）放在文字**之前**，
          是因为它们原本就会盖住整格（连字一起罩）。现在文字挪到它们
          之上后，"闪一下"的时候字反而看得更清楚了 —— 底色照亮，
          但你还认得出是哪个音，这比原来更好。

        ★ v1.5 加了「透视贴合」这一层，但**上面那个顺序一个都没动** ★
          贴合做的事只有两件：把底板换成四边形、在画那八层之前
          `setTransform()` 一下。也就是说 `_paint_rings` 之类压根不知道
          自己在画一个梯形 —— 它们拿到的还是那个方方正正的 `_Frame`，
          只是落在屏幕上的时候被投影了。
          这样"网格变歪了、圆圈没跟着歪"这种**只有走一次变换才不会出**
          的毛病，从结构上就不可能发生。
        """
        if self.hidden:
            return                       # 不画 = 全透明（窗口还在，鼠标照样能抓）
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ★ 「可按」单独开着：浮窗只当琴键用 ★
        #   用户：「可按的时候不要显示高亮」（连着报了两轮）。
        #   开「可按」是要自己动手弹，屏幕上再留着"该打哪个键"的高亮格、
        #   收缩圆圈、序号小圆标，纯属干扰 —— 全收起来，只留 16 个空键位。
        #   （标志由 `control._refresh_keys_only()` 拍板：可按开着**而且**
        #     跟打关着才置真 —— 跟打必须看提示。）
        #   放在最前面：有谱面 / 没谱面 / 演奏结束，三种情况一视同仁。
        # ★ 「训练」模式 ★
        #   放在**最前面**（`keys_only` 之前）—— 训练和「可按」经常一起开，
        #   而 `keys_only` 那条会先 return 掉。
        #   ★ 跟谱面窗**同一套绘制**，只是数据源换成训练进度 ★
        #     用户：「整体和示谱器一样，就是播放改成手动点」。
        #     下面这几行跟正常分支长得几乎一样，差别只有一个：
        #     喂进去的是 `_train_frame()` 而不是 `_frame()`。
        #   ★ 圈照画 ★ —— 它按**真实流逝时间**缩，用来标"这个音到下一个
        #     隔多久"（用户：「需要标记间隔时间的这样子直观」）。
        if self.train_on:
            self._panel(p)
            tf = self._train_frame()
            if tf is None:
                self._hint(p, '训练：没有音了')
                self._paint_flash(p)
                return
            self._paint_cells(p, tf)
            self._paint_hot_flash(p, tf)
            self._paint_flash(p)
            self._paint_rings(p, tf)
            self._paint_labels(p, tf)
            self._paint_badges(p, tf)
            # ★ 这里原来还有一行 `_paint_train_hud(p, tf)` ★
            #   它画的是目标格下面那行 `3 / 60`（练到第几个 / 一共几个）。
            #   用户：「不用显示还有多少个，然后需要后面还有几个的显示」——
            #   那个"还有多少个"说的就是它，去掉。
            #   而"后面还有几个"由 `_paint_badges` 的**序号角标**说
            #   （每个后续音在它自己的格子上挂 `1`/`2`/`3`），那个留着。
            #
            #   两个的区别：`3 / 60` 是**总数**（还剩多少工作量），
            #   角标是**位置**（下一个按哪儿）。训练要的是后者 ——
            #   知道"还剩 57 个"对弹琴没有任何帮助，只会让人分心。
            return

        if self.keys_only:
            self._panel(p)
            self._paint_blank_cells(p)
            # ★ 按下去照样得闪 ★
            #   用户：「可按的时候按下去的按键没有高亮」——
            #   上一版这里直接 `return`，把 `_paint_flash` 一起跳过了。
            #   收起来的是**谱面提示**（"该弹哪个键"），
            #   而"我刚按了哪个"是另一回事，必须留着 ——
            #   手动弹琴时没有这个反馈，就不知道自己到底点中没有。
            #   （`_paint_flash` 现在用 `_geom()` 算位置，不需要 `_frame()`。）
            self._paint_flash(p)
            return

        if not self.timeline or not self.timeline.items:
            self._panel(p)
            # ★ 「可按」打开时把格子也摆出来 ★
            #   不然屏幕上只有一行"还没有谱子"，用户想点都没地方点
            #   （"点了没反应"的一种就是这么来的）。
            if self.pad_click:
                self._paint_blank_cells(p)
            self._hint(p, '还没有谱子')
            # 按下去的青色闪一下照样要画（空字典时它什么都不做，成本为零）
            self._paint_flash(p)
            return

        f = self._frame()
        if f is None:
            self._panel(p)
            if self.pad_click:
                self._paint_blank_cells(p)
            self._hint(p, '演奏结束')
            self._paint_flash(p)          # 同上
            return

        # ★ 底板先画 ★
        #   就是"窗口那么大"的那个圆角矩形 —— 浮窗固定摆放，画满自己这块。
        self._panel(p)
        # （原来这里还有一行 `self._paint_title(p, f)`：网格上方一个
        #   黄字大标题，写着"当前该打的音"。用户：「这里不需要显示数字，
        #   直接把上面的搬到里面去就行」—— 它跟网格里的高亮是同一件事，
        #   删掉之后那 50 px 全给网格。见类头的 `HEADER_H`。）
        self._paint_cells(p, f)
        self._paint_hot_flash(p, f)
        self._paint_flash(p)
        self._paint_rings(p, f)
        self._paint_labels(p, f)
        self._paint_badges(p, f)
        self._paint_countdown(p, f)
        self._paint_unmapped_hint(p, f)

    def _frame(self) -> _Frame | None:
        """把这一帧的几何量和数据算齐（拆出来的各个 `_paint_*` 都吃它）。"""
        if not self.timeline or not self.timeline.items:
            return None
        group = self._current_group()
        if not group:
            return None

        w, h = self.width(), self.height()
        avail_w = w - 2 * self.PAD
        avail_h = h - self.HEADER_H - self.FOOTER_H
        side = min(avail_w, avail_h)
        cell = (side - 3 * T.GAP) / 4.0
        ox = (w - side) / 2.0
        oy = self.HEADER_H + (avail_h - side) / 2.0

        cur_cells, _cur_rest, _cur_name = group[0]
        # 「换音那一下」的闪光 —— 纯谱面驱动，见 `current_blinking()`
        hot_flash = self.current_blinking()
        # 常亮（该弹这个）和闪一下（就是现在）**不冲突**，两个都要显示
        blinking = self.mark_current or hot_flash
        return _Frame(
            w=w, h=h, cell=cell, side=side, ox=ox, oy=oy,
            group=group, timed=self._upcoming_timed(),
            # 每个格子 -> 它在预览里的**所有**序号（0 = 当前）
            # （细节见 `cell_orders()` 的注释 —— 这里踩过"连按同一个键
            #   看不出来"的坑）
            orders=cell_orders(group),
            blinking=blinking, hot_flash=hot_flash, cur_cells=cur_cells,
        )

    def _cell_rect(self, f: _Frame, row: int, col: int,
                   inset: float = 0.0) -> QRectF:
        """格子坐标 -> 屏幕矩形。**行 3 画在最上面**（跟游戏画面一致）。

        `inset` 为正 = 往内缩，为负 = 放大（当前格那点"微微放大"就是
        传了个负数进来）。

        ★ 老老实实四等分 ★
          这里原来读的是 `f.us` / `f.vs`（透视贴合时的"内部分格线"，
          用来把每一格单独对到游戏里某个键上）。贴合整套删掉之后，
          浮窗是固定摆放，格子就该是**等分**的 —— 那是它自己的排版，
          跟游戏里的琴没有关系。
        """
        x = f.ox + col * (f.cell + T.GAP)
        y = f.oy + (3 - row) * (f.cell + T.GAP)
        r = QRectF(x, y, f.cell, f.cell)
        if inset:
            r = r.adjusted(inset, inset, -inset, -inset)
        return r

    # -- ★ 「可按」：点格子出声 ★ --

    def _geom(self) -> tuple[float, float, float]:
        """网格几何 `(ox, oy, cell)` —— **只看窗口大小，不看有没有谱面**。

        ★ 为什么单开一份 ★
          `_frame()` 在"还没载入谱面"和"曲子播完了"这两种情况下返回
          `None`（画画那条路那样处理是对的：没内容就不画）。
          可**点格子**不该跟着失效 —— 格子还在屏幕上，点下去就该响。
          所以这里把 `_frame()` 里那段几何算式抄一份，去掉 timeline 那层。
          （哪天那边的排版改了，这两处得一起改。）
        """
        w, h = self.width(), self.height()
        avail_w = w - 2 * self.PAD
        avail_h = h - self.HEADER_H - self.FOOTER_H
        side = min(avail_w, avail_h)
        cell = (side - 3 * T.GAP) / 4.0
        ox = (w - side) / 2.0
        oy = self.HEADER_H + (avail_h - side) / 2.0
        return ox, oy, cell

    def _cell_at(self, pos) -> tuple[int, int] | None:
        """屏幕坐标 -> (行, 列)；没落在任何格子上就是 `None`。

        ★ 用 `_geom()` 而不是 `_frame()` ★
          见上面那段："点得到"这件事不该被"有没有谱面"绑住。
        """
        ox, oy, cell = self._geom()
        for row in range(4):
            for col in range(4):
                r = QRectF(ox + col * (cell + T.GAP),
                           oy + (3 - row) * (cell + T.GAP), cell, cell)
                if r.contains(pos):
                    return row, col
        return None

    def _paint_blank_cells(self, p: QPainter):
        """摆一副"全空闲"的格子（没谱面 / 演奏结束时用）。

        ★ 只给「可按」用 ★
          平时的空状态就是一块板 + 一行提示（干净）。
          可「可按」打开时用户是要**点着弹**的 —— 格子不画出来，
          他连往哪儿点都不知道（这也是"点了没反应"的一种：
          屏幕上根本没格子）。
        """
        ox, oy, cell = self._geom()
        for row in range(4):
            for col in range(4):
                r = QRectF(ox + col * (cell + T.GAP),
                           oy + (3 - row) * (cell + T.GAP), cell, cell)
                p.setPen(QPen(T.CELL_EDGE, 1.6))
                p.setBrush(QBrush(T.CELL))
                p.drawRoundedRect(r, T.RADIUS, T.RADIUS)
                if not self.show_labels:
                    continue
                p.setPen(QPen(T.TEXT))
                font = QFont()
                font.setPointSizeF(max(10.0, r.width() * 0.26))
                p.setFont(font)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                           layout.PAD_GRID[row][col])

    def _train_frame(self) -> _Frame | None:
        """用**训练进度**造一个 `_Frame` —— 数据源换了，形状跟 `_frame()` 一样。

        用户：「整体和示谱器一样，就是播放改成手动点」。

        ★ 为什么硬造一个 `_Frame`，而不是另写一套绘制 ★
          用户要的是"整体一样"。第一版自己画了一套（只点亮目标格、
          其余压暗），结果底色、字号、角标全是另一套，用户一眼就看出不对。
          喂给**同一套** `_paint_cells` / `_paint_labels` / `_paint_badges`，
          "长得一样"就成了**结构上的保证** —— 以后改配色只改一处。

        ★ `timed` 拿真实流逝时间造，不用曲子的秒 ★
          用户：「然后需要标记间隔时间的这样子直观」。
          训练不看时间，节奏信息就丢了 —— 光知道下一个是哪个键，
          不知道中间该隔多久，练出来的是按键顺序、不是曲子。
          所以每个音配一个间隔，点对之后重新起算，让**收缩圆圈**照常缩：
          圈缩到中心 = "按这个节奏，现在该点下一个了"。
          早点晚点都随你（训练不强制节奏），圈只是那把尺子。

        ★ 几何算式跟 `_frame()` 是同一份 ★
          两处都从"窗口大小"算，所以谱面窗和训练面板并排放着时，
          格子位置、大小完全对得上。（跟 `_geom()` 也是一致的。）
        """
        seq = self.train_seq
        i = self.train_i
        if not seq or i >= len(seq):
            return None

        w, h = self.width(), self.height()
        avail_w = w - 2 * self.PAD
        avail_h = h - self.HEADER_H - self.FOOTER_H
        side = min(avail_w, avail_h)
        cell = (side - 3 * T.GAP) / 4.0
        ox = (w - side) / 2.0
        oy = self.HEADER_H + (avail_h - side) / 2.0

        # 往后看几个 —— 跟谱面窗同一个 `preview_count`
        n = max(1, self.preview_count)
        group = []
        for k in range(i, min(i + n, len(seq))):
            pitch = seq[k]
            c = layout.pitch_to_cell(pitch)
            group.append(([c] if c is not None else [], False, pitch))

        # ★ 不画收缩圈 ★
        #   用户：「要显示下一个按键的倒计时但是不用倒计时只需要显示就行」。
        #   也就是：**只要标出"下一个在哪"，不要那个会动的倒计时**。
        #   所以 `timed` 给空 —— `_paint_rings` 靠它画圈，空了就一个都不画。
        #   "下一个在哪"由**序号角标**说（`orders` 会在 `_paint_badges` 里
        #   画成 `1 2 3…`），那是静态的，不会在屏幕上动来动去。
        #
        #   （上一版这里塞了真实流逝时间、让圈缩着走，想用圈来标"隔多久"。
        #     用户的答复是不要 —— 训练本来就不看时间，圈在那儿缩反而像
        #     在催他。这就跟"播放改成手动点"是一回事：**别催**。）
        timed: list = []

        return _Frame(
            w=w, h=h, cell=cell, side=side, ox=ox, oy=oy,
            group=group, timed=timed,
            orders=cell_orders(group),
            blinking=True,            # 提示层开着：序号角标要显示
            hot_flash=False,          # 没有"换音闪一下"（不按时间走）
            cur_cells=group[0][0],
        )

    # ★ `_paint_train_hud()` 删掉了 ★
    #   它画的是目标格下面那行 `3 / 60`（练到第几个 / 一共几个）。
    #   用户：「不用显示还有多少个，然后需要后面还有几个的显示」——
    #   "还有多少个"说的就是它，去掉。
    #   "后面还有几个"由 `_paint_badges` 的**序号角标**说：每个后续音
    #   在它自己的格子上挂 `1`/`2`/`3`，那个留着。
    #
    #   两个的区别值得记一句：
    #     `3 / 60` 是**总数**（还剩多少工作量）—— 对弹琴没有任何帮助；
    #     角标是**位置**（下一个按哪儿）—— 那才是要看的。
    #   删掉之后目标格下半部正好空出来，`PAD N` 那行反而看得更清楚了。

    def _train_gap_at(self, idx: int) -> float:
        """训练序列里第 `idx` 个音到下一个音的间隔（秒）。

        来源是控制台开训练时算好的 `train_gaps` ——
        就是原谱面里相邻两个 `start_sec` 之差。
        训练不看时间，但**间隔**是曲子的一部分，得留着：
        它就是用户要的"标记间隔时间"。
        """
        if 0 <= idx < len(self.train_gaps):
            return max(0.15, float(self.train_gaps[idx]))
        return max(0.15, float(self.train_gap))

    def train_start(self, i: int, gap: float):
        """控制台点对了一个音 —— 从这一刻重新起算。

        `i` 是新的下标（练到第几个），`gap` 是"这个音到下一个"的间隔。
        """
        self.train_i = int(i)
        self.train_gap = max(0.15, float(gap))
        self.train_t0 = time.monotonic()

    def _on_train_tick(self):
        """训练时那个 16ms 的小定时器 —— 只为了让圈动起来。"""
        if not self.train_on or not self.isVisible():
            self._train_tick_t.stop()
            return
        self.update()

    def mousePressEvent(self, event):
        """「可按」打开时，点哪个格子就出哪个音。

        ★ 平时根本走不到这儿 ★
          浮窗整窗带 `WS_EX_TRANSPARENT`（鼠标完全穿透 —— 枪的准星
          要能直接打过去），事件进不了浮窗里的任何控件。只有控制台
          把穿透取消（「可按」或「跟打」打开时）之后这条路才通，
          所以这里没有"该不该响应"的花样，`pad_click` 就是那个开关。
        """
        if not self.pad_click or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hit = self._cell_at(event.position())
        if hit is None:
            super().mousePressEvent(event)
            return
        row, col = hit
        self.pad_pressed.emit(layout.PAD_GRID[row][col])
        event.accept()

    # -- ★ 顶部大字：已删除 ★ --
    #
    #   这里原来有个 `_paint_title()`：在网格上方画一个黄字大标题，
    #   内容是"当前该打的音"（`5`），休止时是"休止 0.50 秒"。
    #
    #   ★ 用户：「这里不需要显示数字，直接把上面的搬到里面去就行」★
    #     它跟网格里的高亮说的是同一件事 —— 当前格本身就是深橄榄色
    #     （`theme.ACTIVE`），打下去再闪一下（`theme.PRESS`）。
    #     删掉之后 `HEADER_H` 归零，那 50 px 全给了网格，格子更大。
    #
    #   连带一起去掉的还有「休止 X 秒」那行提示：休止期间网格里没有
    #   该亮的格子，本来靠它告诉你要等多久 —— 现在这个信息没有了。
    #   真要找回来的话，画的地方得换个位置（比如网格正中间盖一层），
    #   不能再占顶部一整条。

    # -- 4×4 网格 --

    def _cell_style(self, f: _Frame, rank, cell=None):
        """一个格子该用什么底色 / 边色 / 字色 / 放大值。

        抽出来是因为 `_paint_cells` 和 `_paint_labels` 都要问同一件事，
        而它们现在是**两次独立的循环**（中间隔着收缩圆圈那一层）——
        两边各抄一份判断迟早会抄歪。

        `cell` 是格子的 `(row, col)`，只有判断"当前格是不是连按的开头"
        时才需要 —— 见下面那段。
        """
        # （这里原来有一段「训练模式优先于一切」：目标格亮黄、其余压暗。
        #   §16.70 删掉了 —— 用户要的是"整体和示谱器一样"，
        #   训练面板就该用**同一套**配色，不该自己长一副样子。
        #   现在训练也走下面这些分支，只是 `f` 是 `_train_frame()` 造的。）

        if rank is None or not f.blinking:
            # ★ 实时跟弹时，后面几个音的「淡黄预告格」也要一起关掉 ★
            #   只关当前格是不够的：预告格本身是淡黄 (255,238,158)，
            #   五个格子扑在屏幕上就是一片黄 —— 看着像"黄色残留"，
            #   实际是你还没弹到的音。跟弹要看的是"我刚敲了哪个键"。
            return T.CELL, T.CELL_EDGE, T.TEXT_DIM, 0.0
        if rank == 0:
            # ★★ 连按中的当前格**不要**变成深色 ★★
            #
            #   用户：「连续弹俩下的**不应该立马变色**，有点不好认」，
            #   紧接着补一句「**如果连续弹3下的也是**」。
            #
            #   深色这个外观本来是在说"这个键弹一下就过去了"。
            #   可连按的时候，同一个格子**既要"现在弹"、又要"还要再弹 N 下"** ——
            #   一抹成深色，后面那半句就没了：
            #     · 当前格按规矩**不显示序号**（那块深色本身就是"就是它"）
            #     · `×N` 只缩在左上角一小块
            #   于是 `5 5` 看起来就跟一个普通的单音一模一样，认不出还要再来一下。
            #
            #   所以连按开头的那个格子**保留预告的淡黄底**，换成醒目的亮黄粗边：
            #   一眼就知道"这个键还没弹完"，同时它仍然是"现在该弹的那个"
            #   （圆圈照样在它上面收缩）。
            #
            #   单音不受影响：只有 `repeat_run ≥ 2`（后面还紧跟着同一个键）
            #   才走这一支。
            if (cell is not None
                    and repeat_run(f.group, 0, cell) >= 2):
                # ★ 连按的当前格：**深色打底 + 亮黄粗边** ★
                #   用户：「不要用浅色代替要点的，要深色」——
                #   这句说的是**所有**"要点的"格子，连按这个也不例外。
                #
                #   原来它保留淡黄底，理由是"别让它看起来像弹完了"
                #   （§16.25 那段）。可那个信息现在有**三样更清楚的**
                #   东西在说：亮黄的粗边、右上角的 `×N`、左上角的序号 ——
                #   用不着再拿一整块浅色去喊。而且浅黄那一大块在深色网格
                #   里太抢眼，把"现在点哪"这件事反而糊掉了。
                return (T.ACTIVE, T.PRESS, T.ACTIVE_TEXT,
                        -f.cell * 0.05)          # 仍然微微放大
            return (T.ACTIVE, T.ACTIVE_EDGE, _txt_for(T.ACTIVE),
                    -f.cell * 0.05)              # 当前格微微放大
        # ★★ 预告格：**深色渐变** ★★
        #
        #   用户连着三句，得合起来读：
        #     「不要用浅色代替要点的，要深色」→ 别再用那套淡黄
        #     「还有高亮」                    → 但后面几个也得看得出来
        #     「要渐变色」                    → 而且要有层次
        #
        #   ★ 中间走过一段弯路 ★
        #     第一版照搬了 `T.UPCOMING`（渐淡黄）→ 用户说"要深色"；
        #     我改成清一色的 `T.CELL` → 用户说"还有高亮"。
        #     两句合起来才是完整要求：**深色系里的高亮 + 层次**。
        #     所以现在是 `T.PREVIEW` —— 从 `CELL` 往上走的五档蓝灰，
        #     越近越亮，全程留在深色区间。
        #
        #   ★ 为什么色相选蓝灰不选黄 ★
        #     黄的归"现在点这个"（当前格那条亮黄粗边），
        #     蓝灰的归"接下来还有这些"。两件事两套色相，
        #     一眼就分得开 —— 这跟浮窗那边"黄=谱面提示、青=你按了"
        #     是同一个思路。
        idx = min(rank - 1, len(T.PREVIEW) - 1)
        _fill = T.PREVIEW[idx]
        return (_fill, _fill.lighter(125), T.TEXT, 0.0)

    def _pressure(self, f: _Frame, cell) -> float:
        """「正要点下去」那一格的**紧迫度**：0 = 刚轮到它，1 = 就是现在。

        用户：「正要点下去那个按键不够明显，然后你看看有没有什么办法
        改的更直观，跟音游一样」。

        ★ 音游抓眼的从来不是**颜色**，是**颜色在动** ★
          音符往下落、判定圈往里收 —— 眼睛是被"变化"拽过去的。
          静态那一格再深一点、再亮一点，效果都不如让它"活"起来。
          所以这一层不改底色（用户当初要的是"不刺眼"，见
          `theme.ACTIVE` 的注释），只算一个 0→1 的数，
          由 `_paint_cells` 拿它去驱动**边宽和亮度**：
          轮廓一直在涨，涨到你按下去为止。

        ★ 返回值可以超过 1 ★
          `left < 0`（已经到点、正在收尾巴）时返回 1~2 ——
          那是"命中"那一下，用来看得更清楚（见那边的爆发环）。
          夹在 [0, 2] 里，别让它无限涨。

        ★ 找不到就返回 0 ★
          休止符、或者这一格压根不在 `f.timed` 里（比如只是预告格），
          都不是"正要点下去"的那一格。
        """
        for cells, rest, _name, start, lead in f.timed:
            if rest or cell not in cells:
                continue
            left = start - self.sec
            if left > lead:
                return 0.0               # 还没轮到它（圈都还没开始收）
            if left >= 0.0:
                # 越接近 0 越接近 1。分母用 `lead` 而不是固定值，
                # 快曲子涨得快、慢曲子涨得慢 —— 跟圈的收缩同一个节奏。
                return max(0.0, 1.0 - left / max(0.15, lead))
            # 到点之后：0 → -RING_TAIL 映射成 1 → 2
            return 1.0 + min(1.0, -left / RING_TAIL)
        return 0.0

    def _paint_cells(self, p: QPainter, f: _Frame):
        """格子的底色和边 —— 只有这两样，文字在 `_paint_labels` 里另画。

        ★ 「正要点下去」那一格多一层"压迫感" ★
          见 `_pressure()`：底色不动，但**边会随时间涨粗涨亮**，
          到点那一下再爆一圈。用户要的"跟音游一样"就是这层动态 ——
          静态的深橄榄色再怎么调都不够跳。
        """
        for row in range(4):
            for col in range(4):
                ranks = f.orders.get((row, col)) or []
                rank = ranks[0] if ranks else None
                fill, edge, _txt, inset = self._cell_style(f, rank,
                                                           (row, col))
                hot = rank == 0 and f.blinking
                r = self._cell_rect(f, row, col, inset)
                k = self._pressure(f, (row, col)) if hot else 0.0

                if k > 1.0:
                    # ★ 命中那一下：一圈快速往外扩的环 ★
                    #   音游里"打中了"都有一记明确的爆发，不然按对了也没感觉。
                    #   用 `left` 直接算扩散进度（1 → 2 对应 0 → RING_TAIL），
                    #   不需要额外的动画状态 —— 时间本身就是进度条。
                    trip = k - 1.0
                    halo = r.adjusted(-r.width() * 0.20 * trip,
                                      -r.height() * 0.20 * trip,
                                      r.width() * 0.20 * trip,
                                      r.height() * 0.20 * trip)
                    c = QColor(T.PRESS)
                    c.setAlpha(int(170 * (1.0 - trip)))
                    p.setPen(QPen(self._dim(c), 5.0 * (1.0 - trip) + 1.0))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(halo, T.RADIUS * 1.6, T.RADIUS * 1.6)
                elif k > 0.0:
                    # ★ 逼近：边越来越粗、越来越亮 ★
                    #   3 → 8 px、alpha 120 → 255。上限 8 是量过的：
                    #   再粗就压到格子里的字了（格子 107px，字占中间 40px）。
                    #
                    #   ★ 浅底上得先垫一圈深色 ★
                    #     连按的当前格底色是淡黄 —— 亮黄边压上去等于没画，
                    #     跟 `_paint_rings` 那边同一个道理、同一个阈值。
                    wide = 3.0 + 5.0 * k
                    if fill.lightness() >= 140:
                        p.setPen(QPen(QColor(26, 20, 4, int(200 * k + 40)),
                                      wide + 3.0))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRoundedRect(r, T.RADIUS, T.RADIUS)
                    edge_c = QColor(T.PRESS)
                    edge_c.setAlpha(int(120 + 135 * k))
                    p.setPen(QPen(self._dim(edge_c), wide))
                    p.setBrush(QBrush(self._dim(fill)))
                    p.drawRoundedRect(r, T.RADIUS, T.RADIUS)
                    continue

                p.setPen(QPen(self._dim(edge), 3 if hot else 1.5))
                p.setBrush(QBrush(self._dim(fill)))
                p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

    def _paint_labels(self, p: QPainter, f: _Frame):
        """格子里的两行字：上行音名、下行游戏键面印的 "PAD N"。

        ★ 为什么它排在收缩圆圈**之后** ★
          圆的半径从 0.45 格一路缩到 0，中途必然扫过格子正中 ——
          也就是音名所在的地方。曾经把圈画在文字之上，截图里
          `5 5` 的那两个同心圆正好叠在 "5" 上，字被切得看不清。
          而"对着游戏找哪个键"全靠这两行字，动效不能吃掉信息。
          挪到文字之下后，圈从字的旁边绕过去，两边都清楚。
        """
        if not self.show_labels:
            return
        for row in range(4):
            for col in range(4):
                ranks = f.orders.get((row, col)) or []
                rank = ranks[0] if ranks else None
                _fill, _edge, txt, inset = self._cell_style(f, rank,
                                                            (row, col))
                hot = rank == 0 and f.blinking
                r = self._cell_rect(f, row, col, inset)
                p.setPen(QPen(txt))
                # 两行：上行是音名（谱面用的），下行是游戏键面上印的
                # "PAD N"（对着游戏找键用的）—— 光有音名你对不上键，
                # 光有 PAD 号又对不上谱面，所以两个都标。
                p.setFont(_fit_font(f.cell * 0.32, bold=hot))
                p.drawText(QRectF(r.x(), r.y() + r.height() * 0.05,
                                  r.width(), r.height() * 0.50),
                           Qt.AlignmentFlag.AlignCenter,
                           layout.cell_to_pitch(row, col))
                p.setFont(_fit_font(f.cell * 0.185))
                p.drawText(QRectF(r.x(), r.y() + r.height() * 0.57,
                                  r.width(), r.height() * 0.36),
                           Qt.AlignmentFlag.AlignCenter,
                           layout.PAD_LABELS[row][col])

    # -- ★ 向内收缩的圆圈 ★ --

    def _paint_rings(self, p: QPainter, f: _Frame):
        """★ 用户要的"一个圆圈向内聚集，聚集到中心的点上就是点的时机" ★

        ★ 半径公式：`r = R_MAX × (离这个音还有多久 / lead)` ★
          分母是**这个音自己的** `lead`（= 它跟上一个音的间隔）。
          所以圈从"上一个音那一刻"开始收、到"这个音那一刻"正好到中心 ——
          用户：「可以把那个圆圈往内缩入的速度跟音符速度匹配」。
          对**时间**严格线性 —— 不做任何缓动。
          缓动（比如越到后面缩得越快）看着是更"有劲"，但它会让
          "还剩多久"这件事**读不出来**：你会以为圈还挺大、其实只剩
          一点点时间了。这个圆圈的全部价值就是让人一眼估出剩余时间，
          所以宁可朴素。刚出现时最大、到点缩成中心一点，跟用户描述的
          一模一样。

        ★ 为什么是"还没开始的音"都画，而不是只画第一个预告 ★
          用户：「**点按俩次就是俩个圆圈**」。
          `5 5`（相隔 0.4 秒）在第一个音还没到点之前，两个音都属于
          "还没开始"——它们的 `left` 分别是 0.8 秒和 1.2 秒，
          代进公式就是一大一小两个同心圆：**小的先到中心（先按）、
          大的后到（后按）**，正好是用户要的那个效果。
          如果按"只画下一个"来写，就只剩一个圈，连按看不出来。

        ★ 什么时候不画 ★
          `blinking` 为假 = 实时跟弹模式。那一屏的黄色要整个让给
          "你按了哪个键"的反馈层（`self.flash`），再多一层亮黄圆圈，
          玩家就分不清哪块黄是"该弹"、哪块是"已弹"了。
          这跟 `_cell_style()` 里关掉预告格是**同一个取舍** ——
          那个开关管着三样东西：预告格、当前格常亮、收缩圆圈，
          它们本来就是同一层"谱面提示"。

        ★ 为什么只描边不填充 ★
          格子的底色本身就是一条信息："第几个弹"靠渐淡黄来区分
          （`T.UPCOMING` 从亮到暗）。填充会把它整块盖掉 —— 尤其
          `5 5 5` 这种同一个格子上叠着三个圈的时候，填出来就是
          一个实心大色块，连"这是第几个预告格"都读不出来了。
          描边还有个好处：半径在变，圈的轮廓跟着变，"在缩"这件事
          一眼就能看出来；实心块的大小变化反而没那么显眼。

          至于"填充会压住音名"—— 那条顾虑现在是靠**层次**解决的
          （圈在文字下面，见 `paintEvent`），不是靠不填充。
        """
        if not f.blinking:
            return
        if f.cell <= 10.0:
            # 格子小到这个地步，圆圈和格子里的字会糊成一团；宁可不画，
            # 也别把"该弹哪个键"这个更基本的信息搅浑。
            return

        items = []
        for _cells, rest, _name, start, lead in f.timed:
            if rest:
                continue
            left = start - self.sec
            # ★ 出现窗口用**统一的 `LEAD`**，不用各自的 `lead` ★
            #   用户：「连点三下就显示三个，俩下俩个」。
            #
            #   这两件事必须分开 —— 上一轮把它们绑成了一个 `lead`，
            #   结果连按时屏幕上永远只剩一个圈：
            #     · **什么时候出现**（窗口）→ 统一 `LEAD`（1.2 秒）
            #       大家都提前同样久挂出来，连按的几个圈才会**同时**
            #       套在一起，一眼看出"这个键还要按几下"；
            #     · **缩到中心要多久**（速度）→ 各自的 `lead`
            #       这是 §16.58 用户要的"跟音符速度匹配"，
            #       而且它保证"缩到中心"永远落在音开始那一刻。
            #
            #   绑在一起的话：连按间隔 0.6 秒 → `lead` = 0.6 →
            #   第二个音要等到 `left ≤ 0.6` 才出现，可那会儿第一个圈
            #   早就缩没了 —— 屏幕上一个圈，跟单音没区别。
            #
            #   ★ 中途那一段圈会"停在最大半径不动" ★
            #     `left ∈ (lead, LEAD]` 时 `prog = (left+RING_LAG)/lead > 1`，
            #     被下面那行 `min(1.0, …)` 夹住 → 半径恒等于最大值。
            #     这不是副作用，正是要的效果：先稳稳挂着让人看清有几个圈，
            #     最后 `lead` 秒才真的收拢。
            if left > LEAD or left < -RING_TAIL:
                continue
            items.append((left, _cells, lead))
        # 远的先画、近的后画：连按时小圈压在大圈上，层次一眼分得开。
        # （试过按音高排、按格子排，都不如按"还剩多久"排 —— 它本身就是
        #   "先后"这个词的定义。）
        items.sort(key=lambda kv: -kv[0])

        for left, cells, lead in items:
            # 进度：1 = 刚出现（最大），0 = 聚拢完成
            # ★ `+ RING_LAG` 就是那个"宁晚勿早"的补偿，见它的注释 ★
            # ★ 分母用 `lead`：每个音的收缩时长跟着它自己的间隔走 ★
            #   用户：「可以把那个圆圈往内缩入的速度跟音符速度匹配」。
            #   快的地方（间隔 0.4 秒）圈就 0.4 秒缩完，慢的地方（2 秒）
            #   就 2 秒缩完 —— 而"缩到中心"永远落在音开始那一刻。
            prog = max(0.0, min(1.0, (left + RING_LAG) / lead))
            # 越接近中心越亮、越粗 —— "快了"要一眼看得出来。
            # （亮度用 alpha 而不是换个更亮的颜色：`T.PRESS` 那个亮黄在
            #   深色底上已经是最跳的了，再亮就发白、跟淡黄预告格撞色。）
            near = 1.0 - prog
            wide = f.cell * (RING_W_MIN_FRAC
                             + (RING_W_MAX_FRAC - RING_W_MIN_FRAC) * near)
            alpha = int(RING_A_MIN + (RING_A_MAX - RING_A_MIN) * near)
            # ★ 让**内边缘**缩到中心，而不是圆心 ★
            #   直接线性缩到 0 的话，半径只剩几个像素时**看起来**就已经
            #   "到中心了"，而那会儿 `left` 还剩零点几秒 —— 这是用户感觉
            #   "提前"的第二个来源。让内径（radius − 半个线宽）收到 0，
            #   视觉上的"聚拢完成"就和 `prog == 0` 对齐了。
            r_in = wide * 0.5
            radius = r_in + (f.cell * RING_MAX_FRAC - r_in) * prog
            for (row, col) in cells:
                r = self._cell_rect(f, row, col)
                c = r.center()
                # ★ 垫不垫暗衬，取决于这格现在是深底还是浅底 ★
                #   这一条是**对着截图来回改出来的**，不是拍脑袋：
                #     · 预告格是淡黄 (255,238,158)，亮黄圈直接画上去对比度
                #       极低 —— 整张图缩着看几乎找不到圈在哪儿，而"更明显"
                #       正是这次改动的全部目的；
                #     · 当前格是深橄榄 (96,108,74)，底色本来就压得住亮黄，
                #       再垫一层暗衬反而把那颗"就是现在"的亮点从亮黄
                #       (255,214,74) 压成暗金黄 (~214,183,68)，丢了冲劲。
                #   所以按实际底色分流，而不是一刀切地都垫。
                ranks = f.orders.get((row, col)) or []
                _fill, _edge, _txt, _inset = self._cell_style(
                    f, ranks[0] if ranks else None, (row, col))
                light_bg = _fill.lightness() >= 128

                if radius > 0.6:
                    ring = QRectF(c.x() - radius, c.y() - radius,
                                  radius * 2.0, radius * 2.0)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    # ★ 描边改成"扫描头带拖尾"的渐变 ★
                    #   用户：「把圆圈改成倒计时加渐变色吧」，
                    #   渐变色方向选的是"扫描头带拖尾"。
                    #
                    #   ★ 为什么是**锥形**渐变，不是径向 ★
                    #     圆环上同一个半径的像素颜色是一样的 ——
                    #     径向渐变画上去，整圈就是一个纯色，
                    #     "头"和"尾"根本分不出来。
                    #     锥形渐变按**角度**取色，才能做出
                    #     "一段最亮、顺着转过去越来越淡"的拖尾。
                    #
                    #   起点角度固定 90°（正上方），不跟着时间转 ——
                    #   圈本身已经在缩了，再叠一层旋转会让人以为
                    #   那是在转圈而不是在倒计时。
                    grad = QConicalGradient(c.x(), c.y(), 90.0)
                    grad.setColorAt(0.0, QColor(T.PRESS.red(), T.PRESS.green(),
                                                T.PRESS.blue(), alpha))
                    grad.setColorAt(0.55, QColor(
                        T.PRESS.red(), T.PRESS.green(), T.PRESS.blue(),
                        int(alpha * 0.38)))
                    grad.setColorAt(1.0, QColor(T.PRESS.red(), T.PRESS.green(),
                                                T.PRESS.blue(), 0))
                    if light_bg:
                        # 先垫一圈更粗的深色，再画渐变。
                        # （试过换个更亮的颜色、单纯加大线宽，都不行：
                        #   底色本身就那么亮，亮色的天花板就那么高，
                        #   只能靠明暗对比来把轮廓拉出来。）
                        p.setPen(QPen(QBrush(self._dim(
                            QColor(26, 20, 4, int(alpha * 0.72)))),
                            wide + f.cell * 0.024))
                        p.drawEllipse(ring)
                    p.setPen(QPen(QBrush(grad), wide))
                    p.drawEllipse(ring)
                # ★ 缩得很小的时候，在中心补一个实心亮点 ★
                #   光靠一圈细线，"就是现在"那一下反而最不明显 ——
                #   圆越小，周长越短，一眼扫过去很容易漏掉。
                #   补个点之后，"到点了"从"一个圈"变成"一颗灯"，
                #   眼睛能直接抓住。它也是上面 `RING_TAIL` 那条尾巴的
                #   主角：圈缩完之后，这颗点还要在中心稳稳亮 0.45 秒。
                if prog >= RING_DOT_AT:
                    continue
                dot = f.cell * RING_DOT_FRAC * (1.0 - 0.45 * prog / RING_DOT_AT)
                # 亮点比圆环更亮、更实（+165 而不是 +130）——
                # 它是这条提示线的**终点**，得是整屏最跳的那一点。
                dot_a = int(RING_A_MIN + 165 * (1.0 - prog / RING_DOT_AT))
                p.setPen(Qt.PenStyle.NoPen)
                if light_bg:
                    halo = dot + f.cell * 0.013
                    p.setBrush(QBrush(self._dim(
                        QColor(26, 20, 4, int(dot_a * 0.72)))))
                    p.drawEllipse(QRectF(c.x() - halo, c.y() - halo,
                                         halo * 2.0, halo * 2.0))
                p.setBrush(QBrush(self._dim(
                    QColor(T.PRESS.red(), T.PRESS.green(), T.PRESS.blue(),
                           dot_a))))
                p.drawEllipse(QRectF(c.x() - dot, c.y() - dot,
                                     dot * 2.0, dot * 2.0))

    # -- 序号角标 + `×N` --

    def _paint_badges(self, p: QPainter, f: _Frame):
        """序号角标与连按标 —— **画在圆圈之上**（理由见 `paintEvent`）。

        ★ 序号角标 ★（用户定的显示方案）
            · 当前按的键 **不显示数字** —— 它就是那块深色 + 放大
            · 下一个按的显示 `1`，**圆圈**里
            · 如果这个键要**连着按两下**，在 `1` **下面**写 `×2`
            · 那个音弹过去之后 `×2` 自然消失 ——
              因为它变成"当前格"了，而当前格不标任何东西
        """
        if not f.blinking:
            return
        for row in range(4):
            for col in range(4):
                ranks = f.orders.get((row, col)) or []
                if not ranks:
                    continue
                # 同一个格子可能出现好几次，角标只写**第一次**那个序号 ——
                # 圆圈只管一件事："这个键该在第几个音弹"。
                # 要连按的话下面另有 `×N`（那才是"还要按几下"）。
                rank = ranks[0]
                r = self._cell_rect(f, row, col,
                                    -f.cell * 0.05 if rank == 0 else 0.0)

                if rank == 0:
                    # ★ 连按同一个键时，`×N` 只能标在当前格上 ★
                    #   用户要的"下一个显示 1、1 下面写 ×2"，
                    #   前提是"下一个"是**另一个键**。
                    #   可 `5 5`（连按）时"下一个"就是当前这个键 ——
                    #   没有第二个格子可以挂 `1`，`×2` 也没地方写。
                    #   这时把 `×N` 直接标在当前格左上角（就是序号
                    #   圆圈那个位置）：数字仍然不显示（符合要求），
                    #   但"要连按"看得出来。
                    #
                    # ★ 数的是"**还要**按几下"，**不含当前这一次** ★
                    #   用户：「在切换到需要点俩下的按键上已经点一下了
                    #   就要把 ×2 去掉」——
                    #   当前这个音**正在按**，所以它不该算进"还要按几下"。
                    #
                    #     `5 5`   → 还要 1 下 → 标 `×1`
                    #     `5 5 5` → 还要 2 下 → 标 `×2`
                    #
                    #   这跟"下一个格"上用 `repeat_run(group, 1, …)`
                    #   是**同一个语义**：`×N` = 从这个标记所在的音起，
                    #   还要按 N 下。
                    #
                    # ★ 门槛从 `>= 2` 改成了 `>= 1` ★
                    #   用户后来实测报：「连续弹俩下的不应该立马变色，
                    #   **有点不好认**」，还补了一句「**如果连续弹3下的也是**」。
                    #
                    #   原来 `5 5` 时 `run_here == 1` 就不标了 ——
                    #   而当前格**按规矩不显示序号**，于是那一格看起来
                    #   跟一个普通单音一模一样，根本认不出还要再来一下。
                    #   现在 `5 5` 也标 `×1`（读作"还要按 1 下"）：
                    #   语义没变，只是补上了原先漏掉的那一档。
                    #
                    #   （"连按中不变深色"那条在 `_cell_style()` 里，
                    #     两处合起来才够用：**底色**说"还没弹完"，
                    #     `×N` 说"还差几下"。）
                    run_here = repeat_run(f.group, 0, (row, col)) - 1
                    if run_here >= 1:
                        # ★ 尺寸调过一次 ★
                        #   用户：「改完之后双击和3击的提示变得有点不明显了」。
                        #   圈现在收得快、在格子上待的时间短，那一格的视觉重心
                        #   就落到这块 `×N` 上了 —— 原来 0.42 × 0.24
                        #   （107 px 的格子算下来约 45 × 22）偏小，
                        #   跟格子里那个大音名一比就弱了。
                        #   现在 0.50 × 0.31（约 54 × 33），并且加了深色描边，
                        #   见 `_paint_run`。
                        _paint_run(p, r.x() + r.width() * 0.05,
                                   r.y() + r.height() * 0.05,
                                   min(r.width() * 0.50, 62.0),
                                   min(r.height() * 0.31, 34.0), run_here)
                    continue

                badge = min(f.cell * 0.36, 28.0)
                bx = r.x() + max(3.0, f.cell * 0.05)
                by = r.y() + max(3.0, f.cell * 0.05)
                bcircle = QRectF(bx, by, badge, badge)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(20, 26, 40, 235)))
                p.drawEllipse(bcircle)
                # 序号圆圈里的字：`1`（最近那个）用亮黄，
                # 后面的用白 —— 这里不能用 `T.ACTIVE`，
                # 它是深色，画在深色圆圈上等于看不见
                p.setPen(QPen(T.PRESS if rank == 1
                              else QColor(228, 234, 248)))
                # ★ 圆圈里**只放一个数字** ★
                #   用户看到 `2·3` 的反应是「这个 2*3 又是什么」
                #   —— 看不懂就是设计问题。
                #   那个写法是我为了标"隔开的重复"（同一个键在第 2、3
                #   个音各来一次）加的，但它把两个数字塞进一个圈里，
                #   圆圈就不再是"第几个弹"了。
                #   现在圆圈只管一件事：**这个键该在第几个音弹**。
                p.setFont(_fit_font(badge * 0.60, bold=True))
                p.drawText(bcircle, Qt.AlignmentFlag.AlignCenter, str(rank))

                # ★ 连按提示：写在 `1` 的**下面** ★
                if rank != 1:
                    continue
                # 从"下一个音"起，这个格子连着出现几次（给"下一个"用）
                run_next = (repeat_run(f.group, 1, (row, col))
                            if len(f.group) > 1 else 0)
                if run_next < 2:
                    continue
                # ★ 这里的中间量别叫 `w` / `h` ★
                #   `w`/`h` 在这个类里一直是"窗口宽高"的意思，
                #   借来当徽章尺寸的话，改代码的人会以为自己在动窗口。
                bw = bcircle.width() * 1.20
                bh = bcircle.height() * 0.70
                _paint_run(p, bcircle.center().x() - bw / 2.0,
                           bcircle.bottom() + bh * 0.16, bw, bh, run_next)

    def _paint_countdown(self, p: QPainter, f: _Frame):
        """圈上那个"还剩几秒" —— 用户要的倒计时。

        用户：「把圆圈改成倒计时加渐变色吧」，
        倒计时那一问选的是"圈中间写剩余秒数"。

        ★ 数字放在格子**右上角**，不是正中心 ★
          正中心是音名（`_paint_labels` 画的大字）。数字压上去，就是拿
          "还剩多久"换掉一半"该按哪个键" —— 两个都是要紧信息，
          不能互相盖。右上角本来是空的（左上角给了序号角标），
          而且离圆心最近，视觉上仍然算"圈里"。

        ★ 必须在音名**之后**画 ★
          `paintEvent` 的层次是"圈 → 音名 → 角标"（见那里的大段注释）。
          数字要是跟圈画在一起，会被音名整块盖掉 —— 所以它跟序号角标
          同一层，由 `paintEvent` 在 `_paint_badges` 之后单独调一次。

        ★ 显示范围跟圈**完全对齐** ★
          `left ∈ [0, LEAD]`：跟 `_paint_rings` 用的是同一个窗口，
          所以"屏幕上有几个圈，就有几个倒计时"。
          用户：「全部需要显示的都要有倒计时」——
          要是这里还用各自的 `lead`，连按时就会只有最近那个音有数字，
          套在外面的几个圈光秃秃的。

        ★ 格式 `%.1f` ★
          一位小数就是这套提示的分辨率：`set_time` 是 8 ms 一跳，
          但人的眼睛读不出 0.01 秒，写两位小数只会让人多花时间去看。
        """
        if not f.blinking or f.cell <= 10.0:
            return

        # ★ 同一个格子上可能有好几个圈（连按）—— 数字得竖着排开 ★
        #   用户：「连点三下就显示三个……而且全部需要显示的都要有倒计时」。
        #   三个**圈**套在一起没问题（同心，本来就该这样），
        #   但三个**数字**都挤在右上角就是一团黑，一个都读不出来。
        #   所以先按格子分组，组内按"离得最近的排最上面"从上往下排。
        by_cell: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for _cells, rest, _name, start, lead in f.timed:
            if rest:
                continue
            left = start - self.sec
            if left < 0.0 or left > LEAD:
                continue
            for cell in _cells:
                # 连 `lead` 一起存 —— 数字的颜色要按"还剩几成"算
                by_cell.setdefault(cell, []).append((left, lead))

        for (row, col), lefts in by_cell.items():
            lefts.sort()                      # 最近的排最上面
            r = self._cell_rect(f, row, col)
            bw = max(26.0, f.cell * 0.34)
            bh = max(16.0, f.cell * 0.20)
            gap = max(2.0, f.cell * 0.022)
            for i, (left, lead) in enumerate(lefts):
                box = QRectF(r.right() - bw - f.cell * 0.04,
                             r.top() + f.cell * 0.04 + i * (bh + gap),
                             bw, bh)
                # 深色圆角底 —— 数字得同时压在预告格的深蓝灰、
                # 当前格的深橄榄、以及青色闪光上都看得清，
                # 光靠字色做不到（`_hot_color` 那三档横跨冷到热）。
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(18, 14, 2, 205)))
                p.drawRoundedRect(box, bh * 0.34, bh * 0.34)
                # ★ 数字颜色跟着"还剩几成"走 ★
                #   用户：「给这个倒计时加变色」。
                #   分母用这个音**自己的** `lead`（= 它跟上一个音的间隔），
                #   所以快曲子整体偏暖、慢曲子整体偏冷 ——
                #   读出来的"紧不紧"永远相对**这首曲子**，不是绝对秒数。
                p.setPen(QPen(_hot_color(left / max(0.05, lead))))
                p.setFont(_fit_font(bh * 0.66, bold=True))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, '%.1f' % left)

    # -- 两层闪光 --

    def _paint_hot_flash(self, p: QPainter, f: _Frame):
        """「换音那一下」的闪光 ★ 用户要的"正在播放的按键闪烁" ★

        纯谱面驱动：`sec` 跨过某个音的 `start_sec` 就闪它一下，
        中间不经过音频、不经过识别 —— 所以它闪的时刻就是谱面的时刻，
        不可能背着音频链路的延迟（时钟到浮窗实测 0.0000 秒）。

        和下面那层「按下去闪一下」的区别：
          这一层 = 谱面走到这儿了（**正在播放/该弹的**）
          下面那层 = 程序听到你敲了（音频触发，跟手模式用）
        """
        if not (f.hot_flash and f.cur_cells):
            return
        k = 1.0 - min(1.0, (time.monotonic() - self._cur_stamp)
                      / max(1e-6, self.mark_hold))
        p.setPen(QPen(QColor(T.PRESS.red(), T.PRESS.green(), T.PRESS.blue(),
                             int(40 + 215 * k)), 5.0))
        p.setBrush(QBrush(QColor(T.PRESS.red(), T.PRESS.green(), T.PRESS.blue(),
                                 int(165 * k))))
        for (row, col) in f.cur_cells:
            r = self._cell_rect(f, row, col,
                                -f.cell * 0.06)     # 闪的时候再往外扩一点
            p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

    def _flash_marks(self) -> list[tuple[QRectF, float]]:
        """当前该闪的格子：`[(矩形, 剩余比例 k)]`。

        ★ 用 `_geom()`，**不用** `_frame()` ★
          这一层原来挂在 `_paint_flash(p, f)` 里，位置从 `f` 算。
          可 `_frame()` 在"还没载入谱面"和"曲子播完了"这两种情况下
          返回 `None` —— 而「可按」模式下**恰恰经常就是这两种情况**
          （用户在手动弹，谱面可能压根没在播）。
          于是按下去不闪 —— 这就是用户报的
          「可按的时候按下去的按键没有高亮」。

          改用 `_geom()`（只看窗口大小）之后，跟 `_cell_at()` 同源：
          **"点得到的格子"和"闪着的高亮"永远是同一块地方**，
          不会出现"点中了但高亮画歪了"。

        ★ 过期的条目顺手删掉 ★
          `has_flash()` 里也删一遍，那个是给 8 ms 定时器判断
          "还要不要继续转"用的；两处都删一下没有副作用。
        """
        now = time.monotonic()
        ox, oy, cell = self._geom()
        out: list[tuple[QRectF, float]] = []
        for fp in list(self.flash):
            left = self.flash[fp] - now
            if left <= 0:
                del self.flash[fp]
                continue
            pcell = layout.pitch_to_cell(fp)
            if pcell is None:
                continue
            row, col = pcell
            r = QRectF(ox + col * (cell + T.GAP),
                       oy + (3 - row) * (cell + T.GAP), cell, cell)
            # 闪的时候往外扩一点（跟原来 `_cell_rect(..., -cell*0.03)` 一致）
            r = r.adjusted(-cell * 0.03, -cell * 0.03,
                           cell * 0.03, cell * 0.03)
            out.append((r, min(1.0, left / 0.35)))
        return out

    def _paint_flash(self, p: QPainter):
        """「按下去闪一下」的那层高亮 —— **青色**，不是黄。

        用户：「按下去的时候要**闪烁一下**」，后来实测又报：
        「闪烁的按键配色不对需要改，**感觉那个按下去的按键跟最后一个一样**」。

        ★ 为什么从黄改成青 ★

          原来这层用的是 `T.PRESS`（亮黄 255,214,74），而屏幕上的黄色系
          本来就在说另一件事 —— 预告格是淡黄 (255,238,158)、圆圈是亮黄、
          序号 `1` 也是亮黄，它们统一表示「**谱面告诉你该弹什么**」。

          于是"你刚按的这一下"和"接下来要按的那个"是同一个颜色，
          用户分不出来 —— 他自己描述成"跟最后一个一样"。

          现在按**语义**分成两个色系：

              黄（`T.PRESS` / `T.UPCOMING`）= 谱面提示：该弹哪个、还剩多久
              青（`T.HIT`）               = 你已经按了

          （历史注释里记着这层**本来**就是青的，中间为了"统成一个颜色"
            改成了黄 —— 现在看那个统一是错的：它们不是同一件事。）

        ★ 位置由 `_flash_marks()` 算 ★
          它用 `_geom()` 而不是 `_frame()`，所以没谱面 / 演奏结束时
          照样闪 —— 见那个方法的注释。
        """
        for r, k in self._flash_marks():
            p.setPen(QPen(QColor(T.HIT.red(), T.HIT.green(), T.HIT.blue(),
                                 int(210 * k + 45)), 4.0))
            p.setBrush(QBrush(QColor(T.HIT.red(), T.HIT.green(),
                                     T.HIT.blue(), int(170 * k + 30))))
            p.drawRoundedRect(r, T.RADIUS, T.RADIUS)

    # -- 底部提示 --

    def _paint_unmapped_hint(self, p: QPainter, f: _Frame):
        """谱子里有琴弹不出来的音时，底部给个红框提示。"""
        bad = _unmapped(self.timeline)
        if not bad:
            return
        msg = '琴上没有这些音，会被跳过：' + ' '.join(bad[:6])
        p.setFont(_fit_font(max(9.0, f.h * 0.026)))
        tw = p.fontMetrics().horizontalAdvance(msg) + 24
        box = QRectF((f.w - tw) / 2.0, f.h - 30, tw, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(30, 12, 14, 225)))
        p.drawRoundedRect(box, 6, 6)
        p.setPen(QPen(QColor(255, 130, 130)))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, msg)


# ---------------- 小工具 ----------------

def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return '%g' % x


def _fit_font(size: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(max(7.0, size))
    f.setBold(bold)
    return f


def _txt_for(fill: QColor) -> QColor:
    """格子里的字该用什么颜色 —— **看底色浅还是深**。

    ★ 为什么不能写死一个颜色 ★
      用户报过：连按的当前格上，音名**看不见了**。
      查下来不是被谁盖住 —— 是 `ACTIVE_TEXT`（浅奶油 238,244,226）
      压在 `UPCOMING[0]` 的淡黄 (255,238,158) 上，两个亮度几乎一样，
      等于白字写白纸。
      那个字色本来就是给深橄榄底配的（见 `theme.ACTIVE_TEXT` 的注释
      "深底上用浅字"），可**前面几个预告格和连按的当前格都是浅底** ——
      一条路径改了颜色，另一条忘了跟着改。

    ★ 阈值 140 是拿 `UPCOMING` 那五档量出来的 ★
       (255,238,158) / (222,212,152) → 亮度都 > 190，必须深字
       (192,188,152) / (164,164,152) → 155 上下，深字更稳
       (146,150,154)                 → 145 上下，两种都行，取浅字
                                        跟深橄榄那边统一
    """
    return T.DARK_TEXT if fill.lightness() >= 140 else T.ACTIVE_TEXT


def _hot_color(k: float) -> QColor:
    """倒计时数字的颜色 —— 越接近该按越"烫"。

    用户：「给这个倒计时加变色」。

    `k` 是**剩余比例**：1 = 刚出现，0 = 就是现在。

        1.0 → 青绿 (70, 235, 190)    "还早，不用急"
        0.5 → 亮黄 (255, 214, 74)    "注意了"
        0.0 → 橙红 (255, 92, 56)     "就是现在"

    ★ 为什么挑这三个色 ★
      青绿和橙红在色轮两头、中间过黄 —— 一条直觉上的"冷 → 热"，
      瞟一眼就知道哪个最急，不用去读数字。
      而且青绿（`T.HIT`）和亮黄（`T.PRESS`）这个项目里本来就在用，
      橙红是新的，专给"最急"这一档。

    ★ 为什么不用纯红 ★
      纯红压在深底上太"报警"了 —— 练琴的时候满屏红字很累。
      橙红留了点亮黄的底子，是"烫"，不是"出错"。
    """
    stops = ((1.0, QColor(70, 235, 190)),
             (0.5, QColor(255, 214, 74)),
             (0.0, QColor(255, 92, 56)))
    k = max(0.0, min(1.0, float(k)))
    for i in range(len(stops) - 1):
        hi, c_hi = stops[i]
        lo, c_lo = stops[i + 1]
        if lo <= k <= hi:
            t = 0.0 if hi == lo else (hi - k) / (hi - lo)
            return QColor(int(c_hi.red() + (c_lo.red() - c_hi.red()) * t),
                          int(c_hi.green() + (c_lo.green() - c_hi.green()) * t),
                          int(c_hi.blue() + (c_lo.blue() - c_hi.blue()) * t))
    return QColor(stops[-1][1])


def _paint_run(p: QPainter, x: float, y: float, w: float, h: float, n: int):
    """在给定位置画一个 `×N` —— 这个键要连着按 N 下。

    用户：「如果是连续点俩下在 1 下面写一个 X2」。

    两处调用，位置不同、样式一样：
      · **下一个格**：写在那颗序号圆圈的**下面**
      · **当前格**（连按同一个键时"下一个"就是它自己）：写在左上角，
        也就是序号圆圈本来该待的位置

    ★ 为什么是黄底黑字，不是红底 ★
      上一版做成了红底大徽章。红色是"警告"的语气，会跟当前格那块
      亮黄抢注意力 —— 而这里只是"提示你等下还要按一下"。
      所以用跟当前格同色系的黄底，克制一点。

    ★ 但黄底配黄底会糊 —— 所以必须描一道深色边 ★
      用户：「改完之后双击和3击的提示变得有点不明显了」。
      这块徽章**绝大多数时候画在淡黄底的格子上**（连按的当前格就是
      `T.UPCOMING[0]` 淡黄 #ffee9e），黄底叠黄徽章，边界直接糊在一起，
      远看就是一块黄斑 —— 那才是"不明显"的主因，不是尺寸不够。
      一条深色描边就能把它从底色里切出来，比单纯放大管用得多。
      （放大也一起做了，见 `_paint_badges` 里的调用点。）
    """
    box = QRectF(x, y, w, h)
    p.setPen(QPen(QColor(52, 38, 0), max(1.6, h * 0.10)))
    p.setBrush(QBrush(QColor(255, 206, 48, 252)))
    p.drawRoundedRect(box, h * 0.36, h * 0.36)
    p.setPen(QPen(QColor(24, 18, 0)))
    p.setFont(_fit_font(h * 0.72, bold=True))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, '×%d' % n)


def _unmapped(tl: Timeline) -> list[str]:
    return layout.unmapped_pitches(tl.all_pitches())
