# -*- coding: utf-8 -*-
"""控制台的**界面搭建**部分 —— 从 `ui/control.py` 里搬出来的。

★ 为什么单独一个文件 ★
   `control.py` 一度有 1927 行，其中 `_build()` 一个人就占 426 行
   （全部是"建控件、设属性、塞进布局"），跟事件处理、音频管理、
   文件 IO 挤在一起，翻起来很累。

★ 为什么用 mixin 而不是拆成一个独立的类 ★
   因为 `_wire()` 里有一百多条 `self.btn_xxx.clicked.connect(...)`。
   拆成组合（`self.ui = ControlUI()`）的话那些全都要改成 `self.ui.btn_xxx`，
   一百多处一起动，风险远大于收益。
   mixin 的 `self.xxx` 查找规则不变 —— **`_wire()` 一行都不用改**。

   注意：mixin 必须在 `ControlWindow` 的基类列表**最左边**，
   否则 MRO 找不到这些方法。
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QPushButton, QSlider, QSpinBox,
    QVBoxLayout, QWidget)
from . import appstyle
from .hotkeys import HotkeyEdit


class BuilderMixin:
    """界面搭建：建控件、设属性、塞布局。**这里不接任何信号。**

    信号连接全部留在 `ControlWindow._wire()` —— 那才是它该待的地方，
    而且那样一来搬动这里的方法就不用碰任何 connect。
    """


    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)

        # ---------- 谱面 ----------
        g_sheet = QGroupBox('谱面')
        f = QVBoxLayout(g_sheet)

        row = QHBoxLayout()
        self.cmb_sheet = QComboBox()
        self.cmb_sheet.setMinimumWidth(220)
        # ★ 「新建谱面」是新加的 ★
        #   用户：「这里没有新建铺面选项」。
        #   在那之前想写新曲子只有两条路 —— 把 .txt 丢进 sheets 文件夹
        #   再按「刷新」，或者拿现成的一份「另存为」。都缺"从零开始"。
        btn_new = QPushButton('✚  新建谱面')
        btn_new.setToolTip(
            '从零开始写一份谱子。\n'
            '起个名字 → 存进 sheets 文件夹 → 直接打开制谱器。')
        btn_open = QPushButton('打开…')
        btn_edit = QPushButton('编辑谱面')
        btn_reload = QPushButton('重新载入')
        # ★ 这里原来并排站着两个「跟」，现在只剩一个 ★
        #     📜 跟谱面 —— 按谱面时间走，**你跟着它弹**
        #     🎵 跟手   —— 你弹一下它走一个，**它跟着你**
        #   「跟手」要一直听着游戏的声音，靠不住（BGM 一压就乱认），
        #   用户决定把它、连同「听音记谱」「导入音频」一起拿掉。
        #   留下的这条**压根不听声音**：谱面里每个音都有准确时间，
        #   照着它推浮窗，零延迟、不可能错。
        btn_live = QPushButton('跟谱面')
        # ★ 图标自己画，不是 📜 ★
        #   它是这一排里最后一个彩色 emoji：真机上被系统换成一个
        #   **橙色的小卷轴**，跟旁边「✚ 新建谱面」和刚换掉的
        #   「播放/停止/回到开头」都不是一套东西。
        #   音符的语义也够用 —— "跟音乐有关"，具体意思由按钮文字承担。
        btn_live.setIcon(QIcon(appstyle.note_icon(15)))
        btn_live.setToolTip(
            '浮窗按谱面里的时间自动往前走 —— 你跟着它弹。\n\n'
            '谱面里每个音都有准确的时间，所以这条路零延迟、不可能错，\n'
            '速度也是定死的。\n'
            '适合：先听几遍熟悉节奏，或者照着谱子匀速过一遍。\n\n'
            '（和下面的「▶ 播放」是同一件事，只是入口放在这儿。）')
        for b in (btn_new, btn_open, btn_edit, btn_reload, btn_live):
            b.setFixedHeight(30)
            b.setIconSize(QSize(15, 15))
        row.addWidget(QLabel('曲谱仓库'))
        row.addWidget(self.cmb_sheet, 1)
        row.addWidget(btn_new)
        row.addWidget(btn_open)
        row.addWidget(btn_edit)
        row.addWidget(btn_reload)
        row.addWidget(btn_live)
        f.addLayout(row)

        self.lbl_sheet = QLabel('还没有载入谱面')
        self.lbl_sheet.setWordWrap(True)
        self.lbl_sheet.setObjectName('dim')
        f.addWidget(self.lbl_sheet)

        self.lbl_live = QLabel('跟谱面：没在走')
        self.lbl_live.setObjectName('accent')
        self.lbl_live.setWordWrap(True)
        f.addWidget(self.lbl_live)

        self.btn_new = btn_new
        self.btn_open, self.btn_edit, self.btn_reload = (btn_open, btn_edit,
                                                        btn_reload)
        self.btn_live = btn_live
        box.addWidget(g_sheet)

        # ---------- 播放 ----------
        g_play = QGroupBox('播放')
        fp = QVBoxLayout(g_play)

        prow = QHBoxLayout()
        # ★ 这三颗按钮的图标是自己画的，不是 ▶ / ⏹ / ⏮ 三个字符 ★
        #   它们跟浮窗顶上那颗 ⏸ 是**同一批** Emoji_Presentation 字符：
        #   真机上会被系统换成彩色 emoji（自己截一张图就看得很清楚 ——
        #   「停止」和「回到开头」都变成了两个蓝色小方块），
        #   跟旁边自己画的线性图标完全是两个体系。
        #   浮窗控制条那边早就改成自己画了，控制台一直漏着。
        self.btn_play = QPushButton('播放')
        self.btn_play.setIcon(QIcon(appstyle.play_icon(13)))
        self.btn_stop = QPushButton('停止')
        self.btn_stop.setIcon(QIcon(appstyle.stop_icon(13)))
        self.btn_back = QPushButton('回到开头')
        self.btn_back.setIcon(QIcon(appstyle.back_icon(13)))
        # ★ 「后退 0.5 秒 / 前进 0.5 秒」删掉了 ★
        #   用户：「这俩个按钮不需要了」。
        #   微调位置现在靠下面那根进度条直接拖 —— 想退 3 秒就拖回去 3 秒，
        #   比连按六下按钮快，也不用先心算"按几下"。
        for b in (self.btn_play, self.btn_stop, self.btn_back):
            b.setFixedHeight(32)
            b.setIconSize(QSize(13, 13))
        self.btn_play.setObjectName('primary')
        prow.addWidget(self.btn_play)
        prow.addWidget(self.btn_stop)
        prow.addWidget(self.btn_back)
        prow.addStretch(1)
        fp.addLayout(prow)

        self.sld_prog = QSlider(Qt.Orientation.Horizontal)
        self.sld_prog.setRange(0, 1000)
        fp.addWidget(self.sld_prog)

        self.lbl_time = QLabel('00:00.0 / 00:00.0')
        self.lbl_time.setObjectName('dim')
        fp.addWidget(self.lbl_time)

        # ★ 「播放声音」—— 用户：「多一个选项，播放声音」★
        #   在这之前这条路是**纯时钟**：点 ▶ 只有格子依次亮过去，
        #   一点声音都没有 —— 而上面那句说明写的是「适合先听几遍
        #   熟悉节奏」，没声音根本听不了。
        #   开关在两处都有（这里 + 浮窗顶上那颗 🔊），两边同步。
        self.chk_sound = QCheckBox('播放声音（浮窗往前走的时候跟着出声）')
        self.chk_sound.setChecked(False)
        self.chk_sound.setToolTip(
            '勾上之后：浮窗按谱面时间往前走的时候，用电脑同步把琴音放出来。\n'
            '想「先听几遍熟悉节奏」就靠它 —— 不勾的话只有格子亮、没有声音。\n\n'
            '音色和制谱器的打击垫是同一套（assets/notes 里那 16 个音）。\n'
            '第一次打开会花一两秒载入音源，之后就现成的了。\n\n'
            '浮窗顶上那颗 🔊 是同一个开关，游戏里也能直接点。')
        fp.addWidget(self.chk_sound)

        # ★ 「跟打」和「可按」★
        #   用户：「新增一个跟打和激活为可点按发声的按钮」。
        #   浮窗顶上那两颗按钮是同一个开关，两边同步。
        self.chk_karaoke = QCheckBox('跟打（播放不放原声，点浮窗格子才出声）')
        self.chk_karaoke.setChecked(False)
        self.chk_karaoke.setToolTip(
            '勾上之后：播放的时候不再自动出声，改成你点浮窗上的格子发声。\n'
            '练琴用 —— 听着自己弹的，才听得出来哪儿按错了。\n\n'
            '打开它会顺手把下面的「可按」也打开（不打开就点不了格子）；\n'
            '关掉它，「可按」会一起关掉，浮窗回到鼠标穿透。\n\n'
            '它不会改「播放声音」那个设置 —— 那是个长期开关，\n'
            '跟打只是这一会儿不放原声，两者互不干扰。')
        fp.addWidget(self.chk_karaoke)

        self.chk_pad_click = QCheckBox('可按（浮窗的格子能点着发声）')
        self.chk_pad_click.setChecked(False)
        self.chk_pad_click.setToolTip(
            '勾上之后：浮窗上那 16 个格子可以用鼠标点，点一下出那个音。\n\n'
            '注意：浮窗平时是鼠标穿透的（枪的准星要能打过去），\n'
            '要让格子能点，只能把整窗的穿透取消 —— 所以开着的时候\n'
            '鼠标会被浮窗挡住，点完记得关掉。\n\n'
            '浮窗顶上那颗「可按」是同一个开关，游戏里也能直接点。')
        fp.addWidget(self.chk_pad_click)
        box.addWidget(g_play)

        # ---------- 🎙 播音到游戏麦克风：已删除 ----------
        #
        # 这里原来是一整组「把曲子放给队友听」：把谱面渲染成 wav
        # （`core/render.py` + `core/synth.py`），再经 `soundcard` 送到
        # 指定的**输出设备**（虚拟声卡 → 游戏麦克风那一侧）。
        #
        # 用户：「这个模块删掉」—— 连同 `core/broadcast.py`、
        # `core/render.py`、`core/audio_io.py`、`ui/control_cast.py`
        # 一起拿掉了。
        #
        # ★ 「📜 跟谱面」和「▶ 播放」不受影响 ★
        #   那两条走的是 `ui/overlay.py::Player`（本地听），
        #   跟"送到虚拟声卡"是两回事。
        # ---------- 显示 ----------
        g_view = QGroupBox('显示')
        fv = QFormLayout(g_view)

        self.sp_preview = QSpinBox()
        # ★ 上限从 12 收到 5 ★
        #   用户：「这里目前最大 5，多了没有用」。
        #
        #   这个数管的是"往后画几个音"：预告格、序号角标 `1 2 3…`、
        #   还有收缩圆圈。
        #
        #   §16.58 之后这条更明显了 —— 圈只提前 `lead` 秒出现
        #   （`lead` = 相邻两个音的间隔，上限 1.2 秒），
        #   所以排在第 6 个往后的音**根本没有圈可看**，
        #   只剩左上角一个序号角标，再往后就只是"排着"，读不出先后。
        #   5 个是用户实测下来够用的量。
        #
        #   （§16.72 起预告格改成深色了，不再是渐淡的淡黄 ——
        #    "这是第几个"全靠角标说，跟底色无关。上面那句
        #    "颜色一波比一波淡"是老版本的依据，结论没变。）
        self.sp_preview.setRange(2, 5)
        self.sp_preview.setValue(5)
        self.sp_preview.setSuffix(' 个音')
        # 别让它被 QFormLayout 拉满整行 —— 一个"5 个音"的数值框
        # 撑到六百像素宽，既难看出它是输入框，也白占地方。
        self.sp_preview.setMaximumWidth(120)
        fv.addRow('往后预看', self.sp_preview)

        orow = QHBoxLayout()
        self.sld_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sld_opacity.setRange(10, 100)
        self.sld_opacity.setValue(92)
        self.sld_opacity.setToolTip('整个浮窗的透明度（连音名文字一起变淡）')
        self.lbl_opacity = QLabel('92%')
        self.lbl_opacity.setFixedWidth(42)
        orow.addWidget(self.sld_opacity, 1)
        orow.addWidget(self.lbl_opacity)
        fv.addRow('整体不透明度', orow)

        brow = QHBoxLayout()
        self.sld_bg = QSlider(Qt.Orientation.Horizontal)
        self.sld_bg.setRange(0, 100)
        self.sld_bg.setValue(100)
        self.sld_bg.setToolTip(
            '只调底板 / 格子的浓度，音名文字不受影响。\n'
            '想「几乎看不见框、只留字和高亮」就把它拉低；拉到 0 就只剩字。')
        self.lbl_bg = QLabel('100%')
        self.lbl_bg.setFixedWidth(42)
        brow.addWidget(self.sld_bg, 1)
        brow.addWidget(self.lbl_bg)
        fv.addRow('底板浓度', brow)

        self.chk_labels = QCheckBox('格子上显示音名')
        self.chk_labels.setChecked(True)
        fv.addRow('', self.chk_labels)

        self.chk_overlay = QCheckBox('显示谱面浮窗')
        self.chk_overlay.setChecked(True)
        # 注：这个开关本身放在下面的「谱面窗位置与大小」组里显示
        #     （用户：「把绑定在卡丘窗口改成是否显示」）。

        self.chk_only_game = QCheckBox('只在卡拉彼丘窗口在前台时才显示')
        self.chk_only_game.setChecked(False)   # 默认不隐身，免得以为浮窗坏了
        self.chk_only_game.setToolTip(
            '勾上之后：切出去看网页 / 打字时浮窗自动隐身，回到游戏立刻现形。\n'
            '隐身 ≠ 关闭 —— 全局热键在隐身状态下照样能按。\n'
            '（默认不勾：浮窗一直挂着，跟以前一样）')
        fv.addRow('', self.chk_only_game)

        self.lbl_fg = QLabel('—')
        self.lbl_fg.setObjectName('mute')
        fv.addRow('前台窗口', self.lbl_fg)
        box.addWidget(g_view)

        # ---------- 悬浮窗 ----------
        g_pos = QGroupBox('谱面窗位置与大小')
        fg = QVBoxLayout(g_pos)

        # ★ X / Y / 宽 / 高 四个数字框拿掉了 ★
        #   用户：「浮窗的 X/Y/宽/高 + 微调按钮」清理掉。
        #   位置直接拖谱面窗顶上那个把手（想想也是，谁会去记坐标）；
        #   大小改成一个「大小」滑块，等比缩放，比分别填宽高直观得多。
        #   四个 QSpinBox 对象**照样建出来但不进界面** —— `_apply_geometry`、
        #   配置读写全靠它们，留着最省事。
        self.sp_x = QSpinBox()
        self.sp_y = QSpinBox()
        self.sp_w = QSpinBox()
        self.sp_h = QSpinBox()
        self.sp_x.setRange(-4000, 8000)
        self.sp_y.setRange(-4000, 8000)
        self.sp_w.setRange(180, 3000)
        self.sp_h.setRange(180, 3000)
        self.sp_w.setValue(470)
        self.sp_h.setValue(580)

        self.sp_x.setValue(3000)
        self.sp_y.setValue(60)

        # 大小滑块（按基准尺寸等比例缩放）
        sizerow = QHBoxLayout()
        self.sld_size = QSlider(Qt.Orientation.Horizontal)
        self.sld_size.setRange(50, 250)
        self.sld_size.setValue(100)
        self.sld_size.setMinimumWidth(200)
        self.sld_size.setToolTip(
            '浮窗等比放大 / 缩小（100% = 470×580）。\n'
            '左边小一点不挡游戏画面，右边大一点看得清。')
        self.lbl_size = QLabel('100%　470×580')
        self.lbl_size.setObjectName('dim')
        self.lbl_size.setMinimumWidth(120)
        sizerow.addWidget(QLabel('大小'))
        sizerow.addWidget(self.sld_size, 1)
        sizerow.addWidget(self.lbl_size)
        fg.addLayout(sizerow)

        # ★ 「绑在卡拉彼丘窗口上」整块拿掉了 ★
        #   用户：「把绑定在卡丘窗口改成是否显示」。
        #   跟着游戏窗口按比例漂移听着聪明，实际用起来是：你摆好的位置
        #   会在切分辨率 / 挪窗口时被抢走，还很难说清是谁动的。
        #   现在这个位置就放**浮窗的总开关**（原来在「视图」那一组里）。
        prow2 = QHBoxLayout()
        self.chk_overlay.setToolTip(
            '浮窗的总开关。\n'
            '关掉之后浮窗彻底不露面（全局热键照样在）。\n'
            '绑了「浮窗 显示/隐藏」热键的话，游戏里也能一键切。')
        prow2.addWidget(self.chk_overlay, 1)
        fg.addLayout(prow2)

        # ★ 把手（抓手）★ —— 「谱面窗无法移动」的真正解法
        #   谱面窗整窗鼠标穿透是**必须**的（枪的准星要能打过去），
        #   而穿透是窗口级样式，没法只让顶上一条接收鼠标 ——
        #   所以另开了一个不穿透的小窗口叠在它顶上当把手。
        #
        #   ★ 那个「谱面窗顶上显示拖动手柄」勾选框删了 ★
        #     用户：「这个选项可以去掉，那个地方需要经常显示的」。
        #     手柄现在是**常驻**的。它本来就是浮窗上唯一抓得住的地方 ——
        #     关掉等于"浮窗再也挪不动"，这种会把自己关进死角的开关，
        #     不该摆在界面上等人误触。
        #     （`overlay.set_handle_visible()` 还留着：浮窗整体隐藏 /
        #      临时隐身的时候，手柄仍然要跟着一起收起来。）

        # ★ 按键显示跟随制谱器 ★
        #   用户：「在制谱器播放的时候这个窗口要跟着制谱器来」
        #        「按键显示跟随」。
        frow = QHBoxLayout()
        self.chk_follow_editor = QCheckBox('浮窗按键跟随制谱器（试听时浮窗同步亮键）')
        self.chk_follow_editor.setChecked(True)
        self.chk_follow_editor.setToolTip(
            '打开制谱器试听 / 记录的时候，浮窗上的格子跟着制谱器的播放头走：\n'
            '当前该打哪个键亮黄、后面几个按顺序标 1/2/3/4。\n\n'
            '这样你在游戏里（或者另一块屏上）也能看到该按什么，\n'
            '不用盯着制谱器那个窗口。\n'
            '关掉之后浮窗回到"控制台载入的那份谱面"。')
        frow.addWidget(self.chk_follow_editor, 1)
        fg.addLayout(frow)

        # ★ 「允许拖动（临时取消鼠标穿透）」和「贴到屏幕右上角」都删了 ★
        #   用户：「这个选项也没有用了」／「这个特可以去掉」。
        #   浮窗现在**始终**保持鼠标穿透 —— 那是它的本分（枪要能直接
        #   打过去）；要挪位置就拖顶上那条「⠿」手柄，只有它不穿透。
        #   右上角也不是个特殊位置，拖过去就是了，不值得单开一个按钮。

        # ---------- ★ 透视贴合：已删除 ★ ----------
        #
        # 这里原来是一整组「把 4×4 网格按透视贴到游戏里那台琴上」：
        #   「贴合到游戏里的琴（透视）」勾选框
        #   「✥ 标定四角」—— 拖四个角，`quadToQuad` 把网格投到四边形上
        #   「✦ 自动对键」—— 藏起浮窗抓一帧，按琴键真实边界一次对准
        #   内部分格线（3 竖 + 3 横，逐格对到对应的键）
        #
        # ★ 用户：「自动贴合也删掉，可以调整大小和位置就行了」★
        #   于是连同 `ui/fit.py`、`core/panel.py`、`core/grab.py`
        #   以及 `ui/overlay.py` / `ui/views.py` 里那一整套贴合渲染
        #   一起拿掉了。浮窗回到**固定摆放**：
        #   大小用上面的滑块，位置拖「⠿」手柄或者点「贴到屏幕右上角」。
        #
        # （背景记一笔：那条路走到底也没能让用户满意 ——
        #   静态贴合要手拖四个角；加上"跟着画面动"之后，
        #   增量式估计的误差会线性累积（转 20° 偏 33.7px），
        #   补的"晃完自动重锁"又带来 0.2 秒的卡顿。
        #   用户最后的判断是：不值得，回到简单可靠的固定摆放。）

        # ---------- ★ 跟随画面：已删除 ★ ----------
        #
        # 这里原来有一行：「跟随画面（转视角后自动跟住）」勾选框 + F8 热键。
        # 做法是抓屏 + 稀疏光流，从"浮窗以外"的画面估出相机怎么动，
        # 再把同样的运动加到四角上（相机纯旋转时两帧之间是精确单应，
        # 与场景深度无关，所以它**不需要看见琴**）。
        #
        # ★ 为什么拿掉 ★
        #   它是**增量式**估计：每帧把新的 (A, t) 叠到四角上，
        #   而每帧都带着一点方向固定的偏差 —— 误差会**线性累积**。
        #   拿物理正确的合成运动量过（`tools/eval_drift.py`）：转 20°
        #   平均偏 33.7px、角上偏 59px；换成 6 自由度、收紧 RANSAC 阈值、
        #   关掉光流降采样都压不下去（33.7 → 33.0px）。
        #   补过"晃完之后重新锁一次绝对位置"，但那样每次都要把浮窗
        #   藏起来抓一帧（0.2 秒），用户的原话是「问题很大，有很高的延迟」。
        #   于是连标定一起来的东西里，只有**静态**那部分留下了：
        #   「✥ 标定四角」+「✦ 自动对键」+ 内部分格线 —— 浮窗照样贴在琴上，
        #   只是画面动了它不跟。

        # 微调按钮、右上角按钮、「允许拖动」都拿掉了 ——
        # 挪位置现在**只有一条路**：拖浮窗顶上那条「⠿」手柄。
        #   ★ `\n` 换成 `<br>` ★ —— 详见 `ui/editor.py` 里那段说明：
        #     文本带 `\n` 时 Qt 退回纯文本，`<b>` 会原样显示成标签。
        self.lbl_tip = QLabel(
            '挪位置：鼠标移到谱面窗<b>顶上那条「⠿」</b>上，按住直接拖。<br>'
            '浮窗本体是<b>鼠标穿透</b>的（枪能直接打过去），所以本体抓不住 ——'
            '只有顶上那条手柄不穿透，全靠它。')
        self.lbl_tip.setWordWrap(True)
        self.lbl_tip.setObjectName('dim')
        fg.addWidget(self.lbl_tip)
        box.addWidget(g_pos)

        # ---------- 游戏内快捷键 ----------
        # ★ 整组从界面拿掉了 ★（用户：「这些设置清理掉」）
        #   那几个 HotkeyEdit 对象**照样建出来**：`_apply_hotkeys`、
        #   配置读写都在用它们，界面不显示而已 —— 想找回来随时能加回布局。
        #   （`hk_listen`（听音记谱）随那个功能一起删了；
        #     `hk_follow`（跟随画面）也一样。）
        #   倒计时选值也一样留着（`sp_count`），只是没地方调了。
        self.hk_play = HotkeyEdit()
        self.hk_restart = HotkeyEdit()
        self.hk_toggle = HotkeyEdit()
        self.sp_count = QSpinBox()
        self.sp_count.setRange(0, 15)
        self.sp_count.setValue(3)
        self.sp_count.setSuffix(' 秒')

        box.addStretch(1)

        self.statusBar().showMessage('就绪')


    def _build_sidebar(self):
        """左侧「曲谱」栏 —— 已经保存的曲子列一排，点一下直接载入。

        用户：「再在添加一个侧边栏打开可以挑选已经保存的曲铺」。

        为什么用 QDockWidget 而不是自己搭一块：它可以拖、可以关、
        可以浮出来，宽度还能拉 —— 这些都是白送的，不用自己写。
        载入过的会打勾，一眼看出现在放的是哪一首。
        """
        self.dock_sheets = QDockWidget('曲谱', self)
        self.dock_sheets.setObjectName('dock_sheets')
        self.dock_sheets.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea)

        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(6, 6, 6, 6)
        pv.setSpacing(6)

        self.list_sheets = QListWidget()
        self.list_sheets.setMinimumWidth(190)
        self.list_sheets.setToolTip(
            '仓库里已经保存的曲谱。\n'
            '点一下 = 载入，双击也行（载入之后浮窗马上换过来）。\n\n'
            '想往里加曲子：把 .txt 谱面丢进 sheets 文件夹，\n'
            '然后按下面的「刷新」。')
        self.list_sheets.itemClicked.connect(self._on_sidebar_pick)
        self.list_sheets.itemActivated.connect(self._on_sidebar_pick)
        # ★ 右键菜单要自己接 ★ —— Qt 默认那个是 QListWidget 自带的
        #   （只有"复制"之类没用项），换成我们自己的删除菜单。
        self.list_sheets.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        pv.addWidget(self.list_sheets, 1)

        brow = QHBoxLayout()
        btn_dir = QPushButton('文件夹')
        # ★ 图标是自己画的，不是 📂 ★
        #   跟旁边那颗红色的垃圾桶一样走 `appstyle` 的单色线性图标 ——
        #   原来这里用的是 📂 这个字符，真机上被系统换成一个**明黄色的
        #   文件夹**，跟旁边自己画的那套完全不是一种东西。
        #   （浮窗控制条上早就为同一个理由把 📚/▶/⏸ 都画出来了，
        #     控制台这边一直漏着。）
        btn_dir.setIcon(QIcon(appstyle.folder_icon(16)))
        btn_dir.setIconSize(QSize(16, 16))
        btn_dir.setToolTip('打开 sheets 文件夹（把新谱面丢进去）')
        btn_dir.clicked.connect(self._open_sheets_dir)
        btn_ref = QPushButton('刷新')
        btn_ref.setToolTip('重新扫描 sheets 文件夹')
        btn_ref.clicked.connect(self._scan_sheets)
        # ★ 「🗑 删谱面」—— 用户：「这里得支持删谱面的选项」★
        #   在这之前想删一份谱子只有一条路：点「📂 文件夹」，
        #   自己在资源管理器里找到那个 .txt 删掉，再回来按「刷新」。
        #   列表里能挑不能删，怎么想都不对劲。
        #
        #   ★ 图标换了两次 ★
        #     一开始是 `🗑` 这个字符 —— 用户拿到手第一句话就是
        #     「这个垃圾桶的图标不明显」。真机上它要么被换成彩色 emoji、
        #     要么在窄按钮里挤成一个小方块。现在跟浮窗控制条那几颗一样，
        #     自己画（`appstyle.trash_icon()`），而且是**红色**的：
        #     删除不可逆，它本来就该从一排灰按钮里跳出来。
        self.btn_del_sheet = QPushButton()
        self.btn_del_sheet.setIcon(QIcon(appstyle.trash_icon(16)))
        self.btn_del_sheet.setIconSize(QSize(16, 16))
        self.btn_del_sheet.setFixedWidth(40)
        self.btn_del_sheet.setToolTip(
            '把列表里选中的那份谱面删掉（文件一起删）。\n'
            '删之前会问一次。列表上右键也能删。\n\n'
            '内置的示例谱面删不掉 —— 它在程序自己的目录里，\n'
            '删了下次重新打包 / 更新又回来了。')
        for b in (btn_dir, btn_ref, self.btn_del_sheet):
            b.setFixedHeight(26)
        brow.addWidget(btn_dir)
        brow.addWidget(btn_ref)
        brow.addWidget(self.btn_del_sheet)
        pv.addLayout(brow)

        self.dock_sheets.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,
                           self.dock_sheets)
