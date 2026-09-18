# -*- coding: utf-8 -*-
"""跟手推进器 —— 「你敲一下，浮窗往前走一格」的全部规则都在这里。

★ 为什么值得单独一个模块 ★
    这套规则原来长在 `ui/control.py` 的 `_on_onset()` 里 ——
    一个 Qt 信号槽，几行代码。改一次得重启程序、进游戏弹两下才知道对不对。
    可它偏偏是最经不起错的一块：**多推一格、少推一格都会一直错下去**，
    因为位置是纯累加的，没有任何东西会把它拉回来。

    抽成纯逻辑之后，这些规则第一次能写进测试（`tests/test_follower.py`）：
      · 敲 3 下必须恰好停在第 3 格
      · 抖动（极短时间内的重复上报）不该多推
      · 连按同一个键照样推两格
      · 退到头 / 推到尾不许越界

★ 位置为什么会"一直错下去" ★
    因为它是**纯计数**的：位置 += 1，没有对照物。
    检测器多报一次就永久超前，少报一次就永久落后。

    这不是靠"把检测器调得更准"能解决的 —— 枪声、BGM、自己的回声，
    总有东西能骗过任何一个能量检测器（`core/onset.py` 那边已经
    尽力挡了，见那里的注释）。
    真正的解法是**给用户一个随时能纠正的入口**：
    浮窗上那行「第 5 / 128 个音」让他发现偏了，
    边上的「上一个」就是一键回来。

★ 它会记账 ★
    `kicks` / `pushed` / `blocked` / `gaps` 全留着，控制台上直接显示。
    这样「按一下跳好几格」不用再猜 —— 弹几下，看数字就知道
    是检测器多报了，还是推进器自己出了问题。
"""

from __future__ import annotations

# 两次推进之间的**保底**最小间隔（秒）。
#
# ★ 为什么是 40 ms ★
#   `core/onset.py` 的 `REFRACTORY` 是 55 ms —— 检测器本身就不会给出
#   比这更密的上报。所以这一层正常情况下**永远不会触发**，
#   它只是推进器自己的兜底：万一哪天换个检测器、或者别处补一发
#   `kick()`，位置不至于被一次性推飞。
#
# ★ 那为什么不干脆调大、顺手挡掉「一次敲击报两下」？★
#   因为挡不住：那种重复上报的间隔通常在 100~300 ms
#   （采样衰减期的能量起伏），而人手真正的快速连击最快能到 60 ms ——
#   两个区间是**重叠**的。卡在中间必然要么漏掉连击、要么放过重复。
#   所以那件事只能在**检测器**层解决（`core/onset.py` 里那条
#   「正在往上跳」的判据就是干这个的），推进器这边不重复设卡。
MIN_STEP = 0.04

# 诊断行里保留最近多少次起音间隔
KEEP_GAPS = 10


class Follower:
    """「你敲一下，我走一格」—— 把起音事件翻译成谱面位置。"""

    def __init__(self, count: int, min_step: float = MIN_STEP):
        self.count = max(0, int(count))
        self.min_step = float(min_step)
        # 当前该弹第几个音（0 起）。== count 表示整首都弹完了。
        self.index = 0
        self.kicks = 0            # 一共收到几次起音上报
        self.pushed = 0           # 其中真正推动了位置的
        self.blocked = 0          # 被 min_step 兜底挡掉的
        self.gated = 0            # ★ 被音高闸门拦下的（不推进）★
        self.last_gate = ''       # 最近一次被拦的理由（给界面显示）
        self.manual = 0           # 用户手动「上一个 / 下一个」的次数
        self.gaps: list[float] = []      # 最近几次起音之间的间隔（秒）
        self._last_t: float | None = None

    # ---------------- 查询 ----------------

    @property
    def total(self) -> int:
        return self.count

    @property
    def at_end(self) -> bool:
        """整首都弹完了 —— 再敲也不会往前。"""
        return self.count == 0 or self.index >= self.count

    @property
    def pos(self) -> int:
        """给界面用的「第几个」（1 起；到结尾时夹在 total）。"""
        if self.count == 0:
            return 0
        return min(self.index + 1, self.count)

    def describe(self) -> str:
        if self.count == 0:
            return '没有谱面'
        if self.at_end:
            return '第 %d / %d 个音（弹完了）' % (self.count, self.count)
        return '第 %d / %d 个音' % (self.index + 1, self.count)

    def diag(self) -> str:
        """自检行 —— 「按一下跳几格」全看这一行。

        用户弹几下，把这一行念出来就够了：
          · 「起音 5 次　推进 5 格」          → 一切正常
          · 「起音 9 次　推进 9 格」          → 检测器多报（一次敲击报了两下）
          · 「起音 5 次　推进 5 格　挡掉 4」  → 检测器抖动得很厉害
          · 「起音 5 次　推进 2 格　拦下 3」  → 音高闸门拦多了（谱面或音准不对）
          · 间隔一栏全是 1000+ ms            → 采集根本没实时（声音被积压了）
        """
        head = '起音 %d 次　推进 %d 格' % (self.kicks, self.pushed)
        if self.blocked:
            head += '　挡掉 %d' % self.blocked
        if self.gated:
            head += '　拦下 %d' % self.gated
        if self.manual:
            head += '　手动 %d' % self.manual
        if not self.gaps:
            return head
        recent = self.gaps[-6:]
        return '%s　间隔 %s ms' % (
            head, ' '.join('%.0f' % (x * 1000.0) for x in recent))

    # ---------------- 推进 ----------------

    def kick(self, t: float) -> int:
        """收到一次「敲了一下」。返回**推动了几格**（0 或 1）。"""
        self.kicks += 1
        prev = self._last_t
        if prev is not None:
            self.gaps.append(float(t) - prev)
            if len(self.gaps) > KEEP_GAPS:
                del self.gaps[:-KEEP_GAPS]
            if (float(t) - prev) < self.min_step:
                self.blocked += 1
                return 0                    # 太密，兜底挡掉，且不刷新基准
        self._last_t = float(t)
        return self.forward()

    def press(self) -> int:
        """打击垫/按键**被真实点了一下** —— 不算起音，也不做去抖。

        ★ 为什么这条路不用 `min_step` ★

          `min_step` 是给"**听声音猜**"准备的兜底：一次敲击的能量
          会抖动，可能被上报两三次，所以要用时间把它们隔开。
          那是一条**猜测**的路，猜错了才需要防抖。

          而打击垫的点击是**同步事件** —— 一个点击就是一个点击，
          程序当场知道是哪个键，不存在"同一件事被报两遍"这回事。
          所以这里**不该**去抖：用户连点两下（哪怕只隔 50 ms）
          就是真要按两下，必须走两格。

          把这两条路分开，各自用各自的规则 —— 混在一起的话，
          要么给真实点击加上了没必要的延迟，要么让猜测的路失去兜底。
        """
        self.kicks += 1
        return self.forward()

    def forward(self, manual: bool = False) -> int:
        """往前一格（到结尾就不动）。返回推动了几格。"""
        if self.count == 0 or self.index >= self.count:
            return 0
        self.index += 1
        if manual:
            self.manual += 1
        else:
            self.pushed += 1
        return 1

    def back(self) -> int:
        """往回一格（到头就不动）。返回退了几格。

        ★ 这是整套东西里最重要的一个操作 ★
          位置是纯计数的，多报一次就永久超前 ——
          而 `back()` 是唯一能把它拉回来的东西。
          所以它必须有按钮、有热键，不能藏在菜单里。
        """
        if self.count == 0 or self.index <= 0:
            return 0
        self.index -= 1
        self.manual += 1
        return 1

    def reset(self) -> int:
        """回到第一个音。"""
        self.index = 0
        self.manual += 1
        return 0

    def goto(self, i: int) -> int:
        """跳到指定位置（越界自动夹紧）。"""
        if self.count == 0:
            self.index = 0
        else:
            self.index = max(0, min(int(i), self.count))
        self.manual += 1
        return self.index
