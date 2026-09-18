# -*- coding: utf-8 -*-
"""全局深色主题 —— 把整个应用统一成一套视觉语言。

★ 为什么需要它 ★
   在这之前项目里**没有样式系统**：控制台和制谱器吃系统默认
   （Fusion / Win11 原生，浅灰底），而时间轴、打击垫、浮窗是
   `QPainter` 自绘的深色。结果是"浅色外壳里嵌了几块深色控件" ——
   最刺眼的是谱面文本框，纯白底黑字，在一屋子深色里像贴了一张白纸。

★ 跟 `ui/theme.py` 的分工 ★
   `theme.py` 管**自绘**（QPainter）那一半：浮窗、时间轴、打击垫。
   这里管**Qt 控件**那一半：按钮、下拉、输入框、分组框、滚动条……
   两边的色相是同一套（深蓝紫底 + 青绿强调），
   `theme.HIT` 的那个青绿就是这里的 `ACCENT`。

★ 为什么锁 `Fusion` ★
   Qt6 在 Windows 上的原生风格支持系统深色模式 —— 但 `windows11`
   这个 style 会忽略掉相当一部分 QSS 属性（圆角、内边距、
   子控件），于是同一份样式表在两台机器上长得不一样。
   锁成 `Fusion` 之后外观完全由 QSS 决定，跨机器一致。

★ 图标怎么办 ★
   "对勾"这种东西 QSS 自己画不出来（没有 transform、也不能插字符）。
   所以启动时用 `QPainter` 生成一张小 PNG 丢进临时目录，
   QSS 用绝对路径去引用 —— 这样不用往 `assets/` 塞文件，
   也不用改 PyInstaller 的 spec（少一个打包时才会踩的坑）。
"""

from __future__ import annotations

import os
import tempfile

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF

# ---------------- 调色板 ----------------
#
#   层次从下往上：WINDOW（窗口底）→ PANEL（面板）→ GROUP（分组框）
#               → INPUT（输入框，反而更暗，凹进去）
#
#   全部**不透明**：QSS 里叠半透明会互相透色，算不清楚，
#   浮窗那套半透明色在 `theme.py` 里另有一套。

WINDOW = '#10131b'          # 窗口底
PANEL = '#161a24'           # 面板 / 工具条
PANEL_HI = '#1c2130'        # 面板上的高亮块
GROUP = '#1a1f2c'           # 分组框底
INPUT = '#0d1017'           # 输入框底（比窗口更暗 = 凹进去）
INPUT_HI = '#131824'        # 输入框悬停

EDGE = '#2a3244'            # 常规边框
EDGE_HI = '#3a4560'         # 悬停边框
EDGE_FOCUS = '#4ec9a8'      # 焦点边框

TEXT = '#e6ebf5'            # 正文
TEXT_DIM = '#96a0b8'        # 次要文字（标签、说明）
TEXT_MUTE = '#7b8599'       # 更弱（占位、长说明）—— 别低于这个亮度，
                            #   再暗在深底上就"看不清但又有东西"了

ACCENT = '#46c9a8'          # 强调色：青绿（= theme.HIT 同一色相）
ACCENT_HI = '#5fe0bd'       # 悬停
ACCENT_DIM = '#1f5f50'      # 压下
ACCENT_SOFT = '#173a33'     # 强调色的淡淡打底

GOLD = '#ffd64a'            # 跟 theme.PRESS 一致（"正在发生"）
GOLD_DIM = '#7a6520'
DANGER = '#ff8a8a'          # 危险操作（全删）

SEL_BG = '#26435a'          # 列表项选中
SEL_EDGE = '#3d6b8f'

DISABLED_BG = '#171b25'
DISABLED_TEXT = '#6d7688'   # 禁用态也得读得出来（原来是 #565f72，太暗）

_FONT_STACK = ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI',
               'PingFang SC', 'Noto Sans CJK SC', 'sans-serif')


# ---------------- 运行时生成的小图标 ----------------

_ICON_CACHE: dict[str, str] = {}


def _icon_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), 'kaqiu_piano_ui')
    os.makedirs(d, exist_ok=True)
    return d


def _check_icon(color: str, size: int = 14) -> str:
    """画一个对勾 PNG，返回给 QSS 用的绝对路径（正斜杠）。"""
    key = 'check_%s_%d' % (color.lstrip('#'), size)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        s = size / 14.0
        p.drawPolyline([
            QPointF(3.0 * s, 7.4 * s),
            QPointF(5.9 * s, 10.3 * s),
            QPointF(11.2 * s, 4.2 * s),
        ])
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def _dash_icon(color: str, size: int = 14) -> str:
    """画一个横杠（用于"半选"态，暂时没用上，留着备用）。"""
    key = 'dash_%s_%d' % (color.lstrip('#'), size)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        s = size / 14.0
        p.drawLine(QPointF(3.2 * s, 7.0 * s), QPointF(10.8 * s, 7.0 * s))
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def _arrow_icon(color: str, size: int = 12, up: bool = False) -> str:
    """画一个三角箭头（下拉框 / 数字框用）。

    ★ 为什么非得画出来 ★
      QSS 里有个流传很广的写法，用四条 `border` 拼一个三角：
          width: 0; height: 0;
          border-left: 4px solid transparent; ...
      它在网页 CSS 里成立，但在 Qt 的 QSS 里**对子控件不可靠** ——
      `::down-arrow` 的尺寸是由 style 定的，写 `width: 0` 之后
      很容易变成一个灰色小方块（实测就是这样）。
      老老实实给一张图，最稳。
    """
    key = 'arrow_%s_%d_%d' % (color.lstrip('#'), size, 1 if up else 0)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        h = size - 1.0
        tri = [QPointF(2.0, 3.6), QPointF(h - 1.0, 3.6), QPointF(h / 2.0, h)]
        if up:
            tri = [QPointF(2.0, h - 2.6), QPointF(h - 1.0, h - 2.6),
                   QPointF(h / 2.0, 2.0)]
        p.drawPolygon(QPolygonF(tri))
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def _speaker_icon(color: str, size: int = 16, muted: bool = False) -> str:
    """画一个喇叭 PNG（开着 / 静音两种）。

    ★ 为什么不用 🔊 / 🔇 这两个字符 ★

      它们要靠系统里有彩色 emoji 字体（Segoe UI Emoji）才有字形。
      而它们要待的地方是浮窗顶上那颗 28×22 的按钮 —— 一旦落到
      "字形缺失"的环境（精简系统、改过字体映射、某些远程桌面），
      屏幕上就是两个空心方块，而且**看不出是没显示还是没开**。

      项目里为同一类事栽过一回（见上面 `_arrow_icon`：能画的别指望样式），
      所以这里也老老实实画出来。
    """
    key = 'spk_%s_%d_%d' % (color.lstrip('#'), size, 1 if muted else 0)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 14.0
        col = QColor(color)
        # 箱体 + 喇叭口连成一个多边形，一次画完
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([
            QPointF(2.0 * s, 5.4 * s), QPointF(4.6 * s, 5.4 * s),
            QPointF(8.6 * s, 2.2 * s), QPointF(8.6 * s, 11.8 * s),
            QPointF(4.6 * s, 8.6 * s), QPointF(2.0 * s, 8.6 * s),
        ]))
        pen = QPen(col)
        pen.setWidthF(1.5 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if muted:
            # 右边一个 × —— "这里本来该有声，现在没有"
            p.drawLine(QPointF(9.9 * s, 4.9 * s), QPointF(13.1 * s, 9.1 * s))
            p.drawLine(QPointF(13.1 * s, 4.9 * s), QPointF(9.9 * s, 9.1 * s))
        else:
            # 一道声波弧（开口朝右）
            p.drawArc(QRectF(6.4 * s, 4.5 * s, 4.6 * s, 5.0 * s),
                      -55 * 16, 110 * 16)
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def sound_icon(on: bool, size: int = 16) -> str:
    """「播放声音」那颗按钮上的喇叭图 —— `ui/overlay.py` 的 DragHandle 用。

    开着 = 青绿（跟按钮 `:checked` 的边框同色），关着 = 次要文字灰。
    颜色分得开，所以**不开游戏也能一眼看出现在是哪种状态**。
    """
    return _speaker_icon(ACCENT if on else TEXT_DIM, size, muted=not on)


def _bar_icon(kind: str, color: str, size: int = 16) -> str:
    """浮窗控制条上的小图标：`list` / `play` / `pause`。"""
    key = 'bar_%s_%s_%d' % (kind, color.lstrip('#'), size)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 14.0
        col = QColor(color)
        if kind == 'play':
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([
                QPointF(4.1 * s, 2.8 * s),
                QPointF(11.6 * s, 7.0 * s),
                QPointF(4.1 * s, 11.2 * s),
            ]))
        elif kind == 'pause':
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(4.0 * s, 2.9 * s, 2.3 * s, 8.2 * s),
                              1.0 * s, 1.0 * s)
            p.drawRoundedRect(QRectF(7.7 * s, 2.9 * s, 2.3 * s, 8.2 * s),
                              1.0 * s, 1.0 * s)
        else:                                   # list —— 「选曲」
            pen = QPen(col)
            pen.setWidthF(1.7 * s)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for y in (3.9, 7.0, 10.1):
                p.drawLine(QPointF(2.7 * s, y * s), QPointF(11.3 * s, y * s))
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def bar_icon(kind: str, size: int = 15) -> str:
    """控制条上那几颗按钮的图标 —— `ui/overlay.py::DragHandle` 用。

    ★ 为什么连 📚 / ▶ / ⏸ 这几个字符也要画出来 ★

      真机上它们**不是**字体里那一个字形，而是被系统换成
      **彩色 emoji**（微软雅黑没有，落到 Segoe UI Emoji 上了）：
      截图里选曲那颗是一本彩色的书、暂停那颗是一个蓝色小方块 ——
      压在深色半透明的控制条上很跳，跟旁边自己画的喇叭
      也完全不是一个风格。

      画出来还顺手解决了另一半风险：不赌系统装了什么字体。
    """
    return _bar_icon(kind, TEXT, size)


def _trash_icon(color: str, size: int = 15) -> str:
    """画一个垃圾桶 PNG（曲谱侧栏上那颗「删掉这份谱面」用）。"""
    key = 'trash_%s_%d' % (color.lstrip('#'), size)
    hit = _ICON_CACHE.get(key)
    if hit:
        return hit
    path = os.path.join(_icon_dir(), key + '.png')
    if not os.path.exists(path):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 14.0
        col = QColor(color)
        pen = QPen(col)
        pen.setWidthF(1.5 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # 盖子
        p.drawLine(QPointF(2.2 * s, 3.7 * s), QPointF(11.8 * s, 3.7 * s))
        # 提手
        p.drawPolyline([
            QPointF(5.5 * s, 3.7 * s), QPointF(5.5 * s, 2.1 * s),
            QPointF(8.5 * s, 2.1 * s), QPointF(8.5 * s, 3.7 * s),
        ])
        # 桶身（上宽下窄）
        p.drawPolyline([
            QPointF(3.5 * s, 5.1 * s), QPointF(4.3 * s, 12.1 * s),
            QPointF(9.7 * s, 12.1 * s), QPointF(10.5 * s, 5.1 * s),
        ])
        # 桶身上两条竖纹
        p.drawLine(QPointF(6.0 * s, 6.7 * s), QPointF(6.3 * s, 10.6 * s))
        p.drawLine(QPointF(8.0 * s, 6.7 * s), QPointF(7.7 * s, 10.6 * s))
        p.end()
        pm.save(path, 'PNG')
    out = path.replace('\\', '/')
    _ICON_CACHE[key] = out
    return out


def trash_icon(size: int = 15) -> str:
    """「删掉这份谱面」那颗按钮的垃圾桶 —— `ui/control_build.py` 用。

    ★ 用红色 ★
      删除是不可逆的（`os.remove` 没有回收站），给它一个**危险色**；
      顺带也从一排灰按钮里跳出来，一眼找得到。

    ★ 为什么不用 🗑 这个字符 ★
      跟控制条那几个是同一个理由：真机上它要么被换成彩色 emoji、
      要么在窄按钮里挤成一个看不懂的小方块 —— 用户原话就是
      「这个垃圾桶的图标不明显」。
    """
    return _trash_icon(DANGER, size)


# ---------------- 样式表 ----------------

def build_qss() -> str:
    check_light = _check_icon('#0d1017', 14)      # 绿底上用深色勾
    check_accent = _check_icon(ACCENT, 14)        # 深底上用青色勾
    arrow = _arrow_icon(TEXT_DIM, 12)
    arrow_hi = _arrow_icon(ACCENT_HI, 12)
    # ★ 数字框的箭头要**更小** ★
    #   它那个按钮区是"上下各一半"，控件不到 22px 高时每半边只有
    #   七八像素 —— 11px 的图塞进去会被裁成一个灰方块。
    arrow_up = _arrow_icon(TEXT_DIM, 8, up=True)
    arrow_down = _arrow_icon(TEXT_DIM, 8)
    arrow_up_hi = _arrow_icon(ACCENT_HI, 8, up=True)
    arrow_down_hi = _arrow_icon(ACCENT_HI, 8)

    return """
/* ============ 全局 ============ */
QWidget {
    color: %(TEXT)s;
    selection-background-color: %(SEL_BG)s;
    selection-color: %(TEXT)s;
}
QMainWindow, QDialog {
    background-color: %(WINDOW)s;
}
QMainWindow::separator {
    background-color: %(EDGE)s;
    width: 2px;
    height: 2px;
}
QMainWindow::separator:hover {
    background-color: %(ACCENT_DIM)s;
}
QToolTip {
    background-color: %(PANEL_HI)s;
    color: %(TEXT)s;
    border: 1px solid %(EDGE_HI)s;
    border-radius: 6px;
    padding: 5px 8px;
}

/* ============ 标签 ============ */
QLabel {
    background: transparent;
}
QLabel:disabled {
    color: %(DISABLED_TEXT)s;
}
QLabel#hint, QLabel#dim {
    color: %(TEXT_DIM)s;
}
QLabel#mute {
    color: %(TEXT_MUTE)s;
}
QLabel#pos {
    color: %(TEXT)s;
    font-weight: 600;
}
QLabel#accent {
    color: %(ACCENT_HI)s;
    font-weight: 600;
}

/* ============ 分组框 ============ */
QGroupBox {
    background-color: %(GROUP)s;
    border: 1px solid %(EDGE)s;
    border-radius: 10px;
    margin-top: 16px;
    padding: 14px 14px 12px 14px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 7px;
    color: %(TEXT_DIM)s;
    background-color: %(WINDOW)s;
}

/* ============ 按钮 ============ */
QPushButton {
    background-color: %(PANEL_HI)s;
    color: %(TEXT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 7px;
    padding: 5px 14px;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #232a3c;
    border-color: %(EDGE_HI)s;
}
QPushButton:pressed {
    background-color: #1a2030;
    border-color: %(ACCENT_DIM)s;
}
QPushButton:checked {
    background-color: %(ACCENT_SOFT)s;
    border-color: %(ACCENT)s;
    color: %(ACCENT_HI)s;
}
QPushButton:default {
    border-color: %(ACCENT_DIM)s;
}
QPushButton:focus {
    border-color: %(ACCENT)s;
    outline: none;
}
QPushButton:disabled {
    background-color: %(DISABLED_BG)s;
    color: %(DISABLED_TEXT)s;
    border-color: #232936;
}
/* ---- 浮窗控制条上那几颗小方块按钮（📚 / ▶⏸ / 🔊）---- */
/* ★ 这里必须把 padding 和 min-height 都归零 ★
   上面那条 `QPushButton` 是给普通按钮写的：`padding: 5px 14px` +
   `min-height: 20px`。而控制条上这几颗是 **28 × 22** 的方块 ——
   左右各 14 px 的 padding 一减，内容区宽度直接变成 **-2**，
   于是 `setText()` 的字**一个都画不出来**，按钮只剩一个空框
   （真机上用户看到的就是「这俩按键都没显示出来啊」）。
   `min-height` 也得归零，不然 22 会被撑成 32（= 20 + 10 + 2），
   整条 34 px 高的控制条被按钮顶满。 */
QPushButton#bar {
    padding: 0px;
    min-height: 0px;
}
/* ★ 控制条上那几颗开关勾上时的样子要**一眼看得出来** ★
   「跟打」「可按」是"模式"，不是"点一下就走"的动作 ——
   用户得能扫一眼就知道现在开着没有。
   `#bar` 那条只归零了 padding / min-height，跟这条的
   background / border / color 不冲突，两条会一起生效；
   这里再写一条 `#bar:checked` 是为了把底色压实一点
   （光靠默认那条 `QPushButton:checked` 的淡青底，在游戏画面上偏淡）。 */
QPushButton#bar:checked {
    background-color: #1d4b40;
    border-color: %(ACCENT)s;
    color: %(ACCENT_HI)s;
}
/* ★ 控制条上的按钮点完**不许留焦点框** ★
   踩过的坑（用户连着报了两轮）：
     「可按的时候高亮没有消失，然后可按这个按键没有变亮」

   根子是 `QPushButton:focus`（上面那条，青绿边框）。
   控制条上那颗按钮点一下，它就拿到键盘焦点，于是**一条青绿边框
   一直挂在那儿不消失**，直到你去点别的地方 —— 用户看到的
   「高亮没有消失」就是这个，它跟"这个开关开着没有"长得还特别像，
   于是把真正的 `:checked` 底色给盖过去了。

   修法是两条腿：
     ① 这里把 `#bar:focus` 的边框压回普通（`DragHandle` 上那几颗
        按钮**根本不该拿键盘焦点** —— 那是个 Tool 小窗，没有键盘交互）；
     ② `DragHandle._build_bar()` 里给按钮设 `NoFocus`，从源头上不拿
        —— 见那段注释。
   两条都留着：样式这条挡住别的路径（比如 Tab 键、程序化 setFocus），
   `NoFocus` 那条保证压根不会有焦点。 */
QPushButton#bar:focus {
    border-color: %(EDGE)s;
}
QPushButton#bar:checked:focus {
    background-color: #1d4b40;
    border-color: %(ACCENT)s;
    color: %(ACCENT_HI)s;
}
QPushButton#primary {
    background-color: %(ACCENT_DIM)s;
    border-color: %(ACCENT)s;
    color: #eafff8;
    font-weight: 600;
}
QPushButton#primary:hover {
    background-color: #2a7d68;
}
QPushButton#danger {
    color: %(DANGER)s;
}
QPushButton#danger:hover {
    background-color: #3a1f26;
    border-color: #7a3a44;
}
QPushButton#flat {
    background: transparent;
    border-color: transparent;
    color: %(TEXT_DIM)s;
}
QPushButton#flat:hover {
    background-color: %(PANEL_HI)s;
    color: %(TEXT)s;
}

/* ★ 「手动演奏」那个开关 ★
   它是个 checkable 按钮：关着的时候低调一点，开着的时候整块变红 ——
   用户要的就是"明显一点"，而红色是全世界通用的"正在录"。 */
QPushButton#record {
    background-color: #1e2436;
    border: 1px solid #4a5570;
    color: #c9d3ea;
    font-weight: 600;
    font-size: 11pt;
    border-radius: 9px;
    padding: 8px 16px;
}
QPushButton#record:hover {
    background-color: #262e44;
    border-color: #6d7ea6;
}
/* ★ 三态 ★
   关：上面的深蓝。
   开着但**还没开始录**（"等第一个音"）：亮蓝 ——
     用户：「这个在暂停的时候要自动变换蓝色手动演奏按键」。
     一直顶着红，看着像"已经在录了"，其实那会儿还是待命。
   真在录：红（下面的 `[recording="true"]`）。 */
QPushButton#record:checked {
    background-color: #2b4a7a;
    border: 1px solid #6fa8ff;
    color: #eaf3ff;
}
QPushButton#record:checked:hover {
    background-color: #345a92;
}
QPushButton#record[recording="true"] {
    background-color: #b5382f;
    border: 1px solid #ff8a7a;
    color: #ffffff;
}
QPushButton#record[recording="true"]:hover {
    background-color: #cc4238;
}

/* ============ 文本框 / 编辑器 ============ */
QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: %(INPUT)s;
    color: %(TEXT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 7px;
    padding: 5px 8px;
}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {
    border-color: %(EDGE_HI)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: %(EDGE_FOCUS)s;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    background-color: %(DISABLED_BG)s;
    color: %(DISABLED_TEXT)s;
}

/* ============ 下拉框 ============ */
QComboBox {
    background-color: %(PANEL_HI)s;
    border: 1px solid %(EDGE)s;
    border-radius: 7px;
    padding: 4px 10px;
    min-height: 20px;
}
QComboBox:hover {
    border-color: %(EDGE_HI)s;
    background-color: #232a3c;
}
QComboBox:focus {
    border-color: %(ACCENT)s;
}
QComboBox:disabled {
    background-color: %(DISABLED_BG)s;
    color: %(DISABLED_TEXT)s;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 20px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url(%(arrow)s);
    width: 12px;
    height: 12px;
    margin-right: 6px;
}
QComboBox::down-arrow:hover {
    image: url(%(arrow_hi)s);
}
QComboBox::down-arrow:disabled {
    image: none;
}
QComboBox QAbstractItemView {
    background-color: %(PANEL)s;
    border: 1px solid %(EDGE_HI)s;
    border-radius: 7px;
    padding: 4px;
    outline: none;
    selection-background-color: %(SEL_BG)s;
}
QComboBox QAbstractItemView::item {
    min-height: 22px;
    padding: 2px 8px;
    border-radius: 5px;
}
QComboBox QAbstractItemView::item:hover {
    background-color: %(PANEL_HI)s;
}

/* ============ 复选框 / 单选框 ============ */
QCheckBox, QRadioButton {
    spacing: 8px;
    background: transparent;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: %(DISABLED_TEXT)s;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid %(EDGE_HI)s;
    background-color: %(INPUT)s;
}
QCheckBox::indicator {
    border-radius: 4px;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: %(ACCENT)s;
}
QCheckBox::indicator:checked {
    background-color: %(ACCENT)s;
    border-color: %(ACCENT_HI)s;
    image: url(%(check_light)s);
}
QCheckBox::indicator:checked:hover {
    background-color: %(ACCENT_HI)s;
}
QCheckBox::indicator:disabled {
    background-color: %(DISABLED_BG)s;
    border-color: #2a3040;
}
QRadioButton::indicator:checked {
    background-color: %(ACCENT)s;
    border: 4px solid %(INPUT)s;
    outline: 1px solid %(ACCENT)s;
}

/* ============ 滑块 ============ */
QSlider {
    background: transparent;
}
QSlider::groove:horizontal {
    height: 5px;
    background-color: %(INPUT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background-color: %(ACCENT_DIM)s;
    border: 1px solid %(ACCENT_DIM)s;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: %(ACCENT)s;
    border: 2px solid %(WINDOW)s;
    width: 13px;
    height: 13px;
    margin: -6px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background-color: %(ACCENT_HI)s;
}
QSlider::handle:horizontal:disabled {
    background-color: #3a4152;
}
QSlider::sub-page:horizontal:disabled {
    background-color: #2a3040;
    border-color: #2a3040;
}

/* ============ 滚动条 ============ */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #333c52;
    border-radius: 6px;
    min-height: 30px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #46536f;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #333c52;
    border-radius: 6px;
    min-width: 30px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #46536f;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
    background: none;
    border: none;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* ============ 滚动区域 ============ */
QScrollArea {
    background-color: %(PANEL)s;
    border: 1px solid %(EDGE)s;
    border-radius: 10px;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QAbstractScrollArea::corner {
    background: transparent;
}

/* ============ 列表 / 树 ============ */
QListWidget, QListView, QTreeWidget, QTreeView {
    background-color: %(INPUT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    alternate-background-color: %(PANEL)s;
}
QListWidget::item, QListView::item, QTreeWidget::item {
    padding: 5px 8px;
    border-radius: 6px;
    min-height: 20px;
}
QListWidget::item:hover, QListView::item:hover, QTreeWidget::item:hover {
    background-color: %(PANEL_HI)s;
}
QListWidget::item:selected, QListView::item:selected, QTreeWidget::item:selected {
    background-color: %(SEL_BG)s;
    border: 1px solid %(SEL_EDGE)s;
    color: %(TEXT)s;
}

/* ============ 数字框 ============ */
QSpinBox, QDoubleSpinBox {
    background-color: %(INPUT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 7px;
    padding: 4px 6px;
    min-height: 20px;
    selection-background-color: %(SEL_BG)s;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: %(ACCENT)s;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url(%(arrow_up)s);
    width: 8px;
    height: 8px;
}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {
    image: url(%(arrow_up_hi)s);
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url(%(arrow_down)s);
    width: 8px;
    height: 8px;
}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {
    image: url(%(arrow_down_hi)s);
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: %(PANEL_HI)s;
    border: none;
    width: 16px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    border-top-right-radius: 6px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    border-bottom-right-radius: 6px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #2c3548;
}

/* ============ 选项卡 ============ */
QTabWidget::pane {
    background-color: %(PANEL)s;
    border: 1px solid %(EDGE)s;
    border-radius: 10px;
    top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: %(TEXT_DIM)s;
    border: 1px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 16px;
    margin-right: 3px;
}
QTabBar::tab:hover {
    color: %(TEXT)s;
    background-color: %(PANEL_HI)s;
}
QTabBar::tab:selected {
    background-color: %(PANEL)s;
    color: %(ACCENT_HI)s;
    border-color: %(EDGE)s;
    border-bottom-color: %(PANEL)s;
}

/* ============ 菜单 ============ */
QMenuBar {
    background-color: %(WINDOW)s;
    border-bottom: 1px solid %(EDGE)s;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 11px;
    border-radius: 6px;
}
QMenuBar::item:selected {
    background-color: %(PANEL_HI)s;
}
QMenu {
    background-color: %(PANEL)s;
    border: 1px solid %(EDGE_HI)s;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 26px 6px 12px;
    border-radius: 6px;
}
QMenu::item:selected {
    background-color: %(SEL_BG)s;
}
QMenu::separator {
    height: 1px;
    background-color: %(EDGE)s;
    margin: 5px 8px;
}

/* ============ 停靠窗口 ============ */
QDockWidget {
    color: %(TEXT_DIM)s;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background-color: %(PANEL)s;
    border: 1px solid %(EDGE)s;
    border-radius: 8px;
    padding: 6px 10px;
    text-align: left;
}
QDockWidget > QWidget {
    background-color: %(WINDOW)s;
}

/* ============ 状态栏 ============ */
QStatusBar {
    background-color: %(PANEL)s;
    border-top: 1px solid %(EDGE)s;
    color: %(TEXT_DIM)s;
}
QStatusBar::item {
    border: none;
}

/* ============ 分隔条 ============ */
QSplitter::handle {
    background-color: %(EDGE)s;
}
QSplitter::handle:hover {
    background-color: %(ACCENT_DIM)s;
}

/* ============ 进度条 ============ */
QProgressBar {
    background-color: %(INPUT)s;
    border: 1px solid %(EDGE)s;
    border-radius: 6px;
    text-align: center;
    color: %(TEXT_DIM)s;
}
QProgressBar::chunk {
    background-color: %(ACCENT_DIM)s;
    border-radius: 5px;
}
""" % dict(
        WINDOW=WINDOW, PANEL=PANEL, PANEL_HI=PANEL_HI, GROUP=GROUP,
        INPUT=INPUT, INPUT_HI=INPUT_HI, EDGE=EDGE, EDGE_HI=EDGE_HI,
        EDGE_FOCUS=EDGE_FOCUS, TEXT=TEXT, TEXT_DIM=TEXT_DIM,
        TEXT_MUTE=TEXT_MUTE, ACCENT=ACCENT, ACCENT_HI=ACCENT_HI,
        ACCENT_DIM=ACCENT_DIM, ACCENT_SOFT=ACCENT_SOFT, GOLD=GOLD,
        GOLD_DIM=GOLD_DIM, DANGER=DANGER, SEL_BG=SEL_BG, SEL_EDGE=SEL_EDGE,
        DISABLED_BG=DISABLED_BG, DISABLED_TEXT=DISABLED_TEXT,
        check_light=check_light, check_accent=check_accent,
        arrow=arrow, arrow_hi=arrow_hi,
        arrow_up=arrow_up, arrow_down=arrow_down,
        arrow_up_hi=arrow_up_hi, arrow_down_hi=arrow_down_hi,
    )


# ---------------- 字体 ----------------

def ui_font(point_size: float = 9.5) -> QFont:
    """界面字体 —— 显式点名，别让 Qt 去猜。

    ★ 为什么要显式 ★
      不指定的话 Qt 用系统默认（英文 Segoe UI），中文靠 fallback。
      中英混排时两套字体的 x-height 和基线对不齐，同一行里
      "位置：2.00 秒" 看着会比纯英文那行松一截。
      点名 `Microsoft YaHei UI` 之后中英是同一套字面，行高也稳。
    """
    f = QFont(_FONT_STACK[0])
    f.setFamilies(list(_FONT_STACK))
    f.setPointSizeF(point_size)
    return f


def mono_font(point_size: float = 11.5) -> QFont:
    """等宽字体（谱面文本框用）—— 对齐位置数字时才看得出版面。"""
    f = QFont('Cascadia Mono')
    f.setFamilies(['Cascadia Mono', 'Consolas', 'JetBrains Mono',
                   'DejaVu Sans Mono', 'monospace'])
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(point_size)
    return f


# ---------------- 入口 ----------------

def apply(app) -> bool:
    """给 QApplication 装上主题。幂等，重复调用无害。

    返回是不是**这一趟**装的（方便调用方决定要不要再刷一遍界面）。
    """
    if app is None:
        return False
    if getattr(app, '_kaqiu_style_applied', False):
        return False
    app._kaqiu_style_applied = True
    # ★ 必须在**创建任何控件之前**锁掉 style ★
    #   锁晚了的话已经建好的控件还是按老 style 画的。
    try:
        app.setStyle('Fusion')
    except Exception:
        pass
    app.setFont(ui_font())
    app.setStyleSheet(build_qss())
    return True
