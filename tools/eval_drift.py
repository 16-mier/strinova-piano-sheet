# -*- coding: utf-8 -*-
"""★ 离线测「跟随」的累积误差 ★ —— 晃完之后到底偏了多少。

★ 为什么非离线不可 ★
  真机上"偏了多少"**没有真值**：用户说「晃动的时候严重对不齐」，
  但没人知道那一帧浮窗**应该**在哪。这里拿一张真实游戏帧当素材，
  用**物理正确的合成运动**造出一段晃动的序列 ——
  每一帧浮窗应该在哪是算得出来的，误差就能量化到像素。

★ 运动为什么这么造 ★
  相机**纯旋转**时，两帧之间是精确的单应：

      H = K · R · K⁻¹              （与场景深度无关）

  K 是内参（这里取主点在画面中心、焦距约 0.9×宽），R 是旋转矩阵。
  这是真的物理模型，不是"随便加点位移"。

★ 为什么用单应造、而跟踪用的是仿射 ★
  那正是真实情况：`core/flow.py` 拟合的是 `estimateAffinePartial2D`
  （旋转 + 缩放 + 平移，**没有剪切**），而真实运动是单应。
  小角度下两者接近；转多了，那点差值就是**系统性偏差** ——
  而系统性偏差会**一帧一帧累积**（每帧都按 `A, t` 更新四角，
  没有任何绝对参考把它拉回来）。这个工具量的正是这件事。

用法：
    python tools/eval_drift.py                     # 默认：1 秒内水平转 20°
    python tools/eval_drift.py --deg 40 --frames 40
    python tools/eval_drift.py --margin 120        # 试试更小的跟踪范围
    python tools/eval_drift.py --frame 存图.png
输出：
    eval_drift.png    误差曲线 + 最后一帧的叠加对比
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from core import flow, motion                                  # noqa: E402

DEFAULT_FRAME = os.path.join(ROOT, '_autofit_frame.png')

# 重锁模拟里"这一帧稳住了"的判据 —— 跟 `ui/track.py` 的两个常量同义。
# 真实系统挑"稳下来那一刻"重锁：那会儿抓帧量得最准，用户也最不容易察觉。
RELOCK_STILL_PX = 3.0
RELOCK_STILL_FRAMES = 3

# 模拟的"浮窗"（图片坐标）。用的是一张真实帧里琴面板 16 键的位置 ——
# 它是画面里**最近的那块东西**，视差问题从这里来。
QUAD0 = [[1572, 590], [1972, 590], [1972, 1229], [1572, 1229]]


# ------------------------------------------------------------------ 合成

def intrinsic(w: int, h: int, f_ratio: float = 0.9) -> np.ndarray:
    """相机内参：主点在画面中心，焦距取画面宽度的一个比例。

    `f_ratio` 直接决定"同样的旋转角在画面上走多远"：
    实测他的屏幕是 5120×2160、游戏窗口 3429 宽，那种宽度下
    0.9 倍焦距大致对应一支中等视场的镜头 —— 和实拍帧里
    "转一下视角琴就跑掉小半屏"的量级对得上。
    """
    f = f_ratio * w
    return np.array([[f, 0.0, w / 2.0],
                     [0.0, f, h / 2.0],
                     [0.0, 0.0, 1.0]], dtype=np.float64)


def rot(axis: str, deg: float) -> np.ndarray:
    """绕某根轴的旋转矩阵。`y` = 水平转视角（摇头），`x` = 上下看。"""
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    if axis == 'y':
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    if axis == 'x':
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])         # 'z' 滚转


def warp(frame: np.ndarray, H: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    return cv2.warpPerspective(frame, H, (w, h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)


def apply_H(pts, H: np.ndarray) -> np.ndarray:
    """把点按单应拍过去（`ui/track.py` 那边只有仿射版本，这里要单应）。"""
    p = np.hstack([np.asarray(pts, dtype=np.float64),
                   np.ones((len(pts), 1))])
    q = p @ H.T
    return q[:, :2] / q[:, 2:3]


def angle_seq(deg: float, n: int, pause: int, run: int = 5) -> list[float]:
    """转 `run` 帧、停 `pause` 帧，循环。`pause=0` 就是匀速转到底。

    ★ 为什么仿真必须有停顿 ★
      真实的"晃"不是匀速转到底 —— 用户是"转两下、停下来看看琴"。
      而重锁**只在停顿时发生**（`ui/track.py::RELOCK_*`：走够一段路
      **且**画面稳住才锁）。匀速转的序列里永远没有"稳住"这一帧，
      于是重锁一次都不触发、也就量不出它的收益 —— 第一版就栽在这。
    """
    out, a = [], 0.0
    step = deg / float(max(n, 1))
    while len(out) < n:
        for _ in range(run):
            a += step
            out.append(a)
            if len(out) >= n:
                return out
        for _ in range(pause):
            out.append(a)
            if len(out) >= n:
                return out
    return out


# ------------------------------------------------- ★ 对照实验：单应 ★
#
# 假设：真实运动是**单应**（8 自由度），而 `core/flow.py` 拟合的是
# **相似变换**（4 自由度：旋转 + 均匀缩放 + 平移）。单应里有**透视畸变**
# 那一项，相似变换表达不了 —— 于是每帧都留下一小撮**方向固定**的残余，
# 一帧一帧叠起来就是"晃两下就严重对不齐"。
#
# 这一节就是拿来验这个假设的：同一套前面步骤（找角点 → LK），
# 只换最后的拟合模型。

def flow_h(a_u8, b_u8, mask=None, scale: float = 1.0,
           max_pts: int = flow.MAX_PTS):
    """拟 8 自由度单应。返回 `(H, n_inlier, info)`（尺度已还原）。"""
    info = dict(n_found=0, n_inlier=0)
    if scale != 1.0:
        a_u8 = cv2.resize(a_u8, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_AREA)
        b_u8 = cv2.resize(b_u8, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_AREA)
        if mask is not None:
            mask = cv2.resize(mask, (a_u8.shape[1], a_u8.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
    pts = cv2.goodFeaturesToTrack(a_u8, max_pts, flow.QUALITY, flow.MIN_DIST,
                                  mask=mask, blockSize=flow.BLOCK)
    if pts is None or len(pts) < flow.MIN_PTS:
        info['why'] = '特征点太少'
        return None, 0, info
    info['n_found'] = len(pts)
    nxt, status, _e = cv2.calcOpticalFlowPyrLK(
        a_u8, b_u8, pts, None, winSize=flow.WIN, maxLevel=flow.MAX_LEVEL,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    good = status.ravel() == 1
    if int(good.sum()) < flow.MIN_PTS:
        info['why'] = '跟住的特征点太少'
        return None, 0, info
    Hm, inl = cv2.findHomography(pts[good], nxt[good], cv2.RANSAC,
                                 flow.RANSAC_REPROJ, maxIters=2000,
                                 confidence=0.995)
    info['n_inlier'] = 0 if inl is None else int(inl.sum())
    if Hm is None or info['n_inlier'] < flow.MIN_INLIER:
        info['why'] = 'RANSAC 没凑出一致的内点'
        return None, info['n_inlier'], info
    if scale != 1.0:
        # ★ 缩图上的单应要还原回原尺度 ★
        #   `H_full = S⁻¹ · H_small · S`，`S = diag(s, s, 1)`：
        #   原图点 p → 缩图 Sp → H_small 映到 q' → 原图 q = S⁻¹q'。
        S = np.diag([scale, scale, 1.0])
        Hm = np.linalg.inv(S) @ Hm @ S
    return Hm, info['n_inlier'], info


# ------------------------------------------------------------------ 评估

def run(frame_path: str, deg: float, n_frames: int, axis: str,
        scale: float, margin: int, quiet: bool = False,
        model: str = 'affine', full: bool = False,
        relock_travel: float = 0.0, pause: int = 0):
    # ★ 必须 `IMREAD_GRAYSCALE` ★
    #   `flow_affine()` 要的是**单通道** uint8（`goodFeaturesToTrack`
    #   和 `calcOpticalFlowPyrLK` 都只吃单通道）。直接喂 3 通道的话
    #   特征点是 0 个、每帧都被判"特征点太少" —— 表现是"全部跟丢"，
    #   而那个数字看起来很像是**算法**不行。
    frame = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
    if frame is None:
        print('读不到图：%s' % frame_path)
        return None
    h, w = frame.shape[:2]
    K = intrinsic(w, h)
    Kinv = np.linalg.inv(K)

    # 跟踪区域：和 `ui/track.py::capture_region` 一个道理 ——
    # 罩住"浮窗"再往外扩一圈，扩多少就是 `--margin`。
    xs = [p[0] for p in QUAD0]
    ys = [p[1] for p in QUAD0]
    x0 = max(0, int(min(xs) - margin))
    y0 = max(0, int(min(ys) - margin))
    x1 = min(w, int(max(xs) + margin))
    y1 = min(h, int(max(ys) + margin))
    ex = (int(min(xs)) - x0, int(min(ys)) - y0,
          int(max(xs)) - x0, int(max(ys)) - y0)
    if not quiet:
        print('帧 %s  %dx%d   内参 f=%.0f' % (os.path.basename(frame_path),
                                              w, h, K[0, 0]))
        print('跟踪区域 %dx%d（外扩 %d）  浮窗占 %.0f%%'
              % (x1 - x0, y1 - y0, margin,
                 100.0 * (max(xs) - min(xs)) * (max(ys) - min(ys))
                 / max(1, (x1 - x0) * (y1 - y0))))

    prev = frame[y0:y1, x0:x1].copy()
    mask = flow.make_mask(prev.shape[:2], exclude=ex)
    quad = np.array(QUAD0, dtype=np.float64)

    travel = 0.0            # 自上次"重锁"以来累计走了多少像素
    still = 0               # 连续几帧"几乎没动"
    relocks = 0
    angles = angle_seq(deg, n_frames, pause)
    rows = []
    for i in range(1, n_frames + 1):
        angle = angles[i - 1]
        H = K @ rot(axis, angle) @ Kinv
        cur_full = warp(frame, H)
        cur = cur_full[y0:y1, x0:x1]

        step = 0.0
        if model == 'h':
            Hm, _n_in, info = flow_h(prev, cur, mask=mask, scale=scale)
            ok = Hm is not None
            why = info.get('why', '')
            if ok:
                before = quad.copy()
                quad = apply_H(quad.tolist(), Hm)
                step = float(np.hypot(*(quad - before).T).max())
        else:
            A, t, info = flow.flow_affine(prev, cur, mask=mask, scale=scale,
                                          full_affine=full)
            ok, why = ((False, '没算出来') if A is None
                       else flow.healthy(A, t, info))
            if ok:
                before = quad.copy()
                quad = np.asarray(
                    motion.apply_to_points(quad.tolist(), A, t),
                    dtype=np.float64)
                step = float(np.hypot(*(quad - before).T).max())

        # 真值：初始四角被 H 拍过去
        truth = apply_H(QUAD0, H)

        # ★ 重锁模拟 ★
        #   真实系统里重锁 = 藏起浮窗抓一帧、按琴键的真实边界重新量一次 ——
        #   量出来的是**绝对**位置，所以误差当场清零。
        #   这里直接拿真值顶替"量出来的结果"：这个工具要回答的是
        #   "定期重锁能不能把累积的账清掉"，而不是"量得准不准"
        #   （那是 `core/panel.py` 和真机冒烟的事）。
        if relock_travel > 0:
            # ★ 和 `ui/track.py::_track_travel()` 同一套判据 ★
            #   一段一段对着改：死区（稳住的那几帧不进路程账）、
            #   路程够了 + 连续稳住 3 帧才锁。两边不一致的话，
            #   这个工具量出来的收益就不是线上那套逻辑的收益了。
            if step >= RELOCK_STILL_PX:
                travel += step
                still = 0
            else:
                still += 1
            if travel >= relock_travel and still >= RELOCK_STILL_FRAMES:
                quad = truth.copy()
                travel = 0.0
                still = 0
                relocks += 1

        err = np.hypot(*(quad - truth).T)
        rows.append(dict(i=i, angle=angle, ok=bool(ok),
                         mean=float(err.mean()), mx=float(err.max()),
                         n_in=info.get('n_inlier', 0), why=why))
        prev = cur

    if not quiet:
        print('\n  帧   视角    本次估计     平均误差   最大误差   内点')
        for r in rows:
            print('  %3d  %+5.1f°  %-8s  %7.1f px %7.1f px  %4d'
                  % (r['i'], r['angle'], 'ok' if r['ok'] else 'skip',
                     r['mean'], r['mx'], r['n_in']))
        lost = [r for r in rows if not r['ok']]
        print('\n跟丢 %d / %d 帧   最终平均误差 %.1f px   最大角误差 %.1f px'
              % (len(lost), len(rows), rows[-1]['mean'], rows[-1]['mx']))
        if relocks:
            print('★ 中途重锁过 %d 次 —— 每次的重锁点误差会掉回 0（绝对定位）★'
                  % relocks)
    return dict(rows=rows, quad=quad, truth=apply_H(QUAD0,
                                                    K @ rot(axis, deg) @ Kinv),
                frame=frame, region=(x0, y0, x1, y1), relocks=relocks)


def main() -> int:
    def opt(name, cast, default):
        if name in sys.argv:
            return cast(sys.argv[sys.argv.index(name) + 1])
        return default

    frame_path = opt('--frame', str, DEFAULT_FRAME)
    deg = opt('--deg', float, 20.0)
    n = opt('--frames', int, 22)
    axis = opt('--axis', str, 'y')
    scale = opt('--scale', float, 0.5)
    margin = opt('--margin', int, 260)
    model = opt('--model', str, 'affine')
    reproj = opt('--reproj', float, None)
    full = '--full' in sys.argv
    if reproj is not None:
        flow.RANSAC_REPROJ = reproj          # 扫参数用，跑完就改回来
        print('RANSAC_REPROJ = %.2f' % reproj)

    res = run(frame_path, deg, n, axis, scale, margin, model=model, full=full,
              relock_travel=opt('--relock', float, 0.0),
              pause=opt('--pause', int, 0))
    if res is None:
        return 1

    # 画一张曲线 + 最后一帧的叠加对比
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    rcParams['axes.unicode_minus'] = False

    rows = res['rows']
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].plot([r['i'] for r in rows], [r['mean'] for r in rows], 'o-',
               label='平均误差')
    ax[0].plot([r['i'] for r in rows], [r['mx'] for r in rows], 's--',
               label='最大角误差')
    ax[0].set_xlabel('帧')
    ax[0].set_ylabel('px')
    ax[0].set_title('跟随误差随时间（累积）')
    ax[0].grid(alpha=.3)
    ax[0].legend()

    x0, y0, x1, y1 = res['region']
    W, Hh = res['frame'].shape[1], res['frame'].shape[0]
    K2 = intrinsic(W, Hh)
    H = K2 @ rot(axis, rows[-1]['angle']) @ np.linalg.inv(K2)
    last = warp(res['frame'], H)[y0:y1, x0:x1]
    ax[1].imshow(last, cmap='gray')
    for name, q, color in (('跟随结果', res['quad'], '#39ff88'),
                           ('真值', res['truth'], '#ff4444')):
        qq = np.asarray(q) - [x0, y0]
        ax[1].plot(np.r_[qq[:, 0], qq[0, 0]], np.r_[qq[:, 1], qq[0, 1]],
                   color=color, lw=2, label=name)
    ax[1].legend()
    ax[1].set_title('最后一帧：绿=跟随，红=真值（%s）' % model)
    fig.tight_layout()
    out = os.path.join(ROOT, 'eval_drift.png')
    fig.savefig(out, dpi=110)
    print('已存 %s' % os.path.basename(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
