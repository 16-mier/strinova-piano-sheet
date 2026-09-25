// 制谱页的时间轴编辑器。
//
// ★ 这一版跟第一版的三个区别（都是用户实测之后提的）★
//
//   一、**方块等宽**，不再跟时值走
//       用户：「时间轴的方块时长不是固定的」。
//       第一版学的是剪辑软件：方块宽度 = 这个音的时值（长的音方块长）。
//       那在音频编辑里是对的，可这是**按键序列** —— 用户要读的是
//       "第几个音、在哪儿"，长度忽长忽短反而看不清节奏。
//       现在每个音一个固定宽度的方块，**位置**照旧反映时间。
//
//   二、**横向滚动交给浏览器**（`overflow-x: auto`）
//       用户：「没办法拖动时间轴」。
//       第一版是自己算 scrollX、自己拦 pointermove —— 在手机上不跟手
//       （没有惯性、跟浏览器的手势也打架）。
//       现在画布直接铺成"内容那么宽"，外面套一个原生滚动容器，
//       甩动、惯性、边缘回弹全部免费。
//
//   三、**长按才进入拖动**
//       既然滚动交给浏览器了，手指按在方块上左右滑到底是"滚"还是"拖方块"
//       就冲突了。所以：**按住 250ms 才变成拖方块**
//       （会有一次轻微震动反馈），之前移动就算滚动。
//       这也是手机上"拖东西"的通用做法。
//
//   ★ 时间线 ★
//     标尺上有数字刻度，轨道区里有贯穿的竖向网格线 —— 用户说
//     「也没有时间线」，第一版那几条太淡了。

import { pitch_to_cell, PAD_GRID } from './layout.js';

export const RULER_H = 30;      // 顶部标尺（放时间数字 + 播放头把手）
export const ROW_H = 40;        // 每条轨道的高度
export const HEADER_W = 48;     // 左侧轨道头的宽度
export const NOTE_W = 36;       // ★ 方块固定宽度 ★
export const DEFAULT_LANES = 6;
export const LANES_MAX = 12;

const PAD_RIGHT = 90;           // 右边留一截，最后一个音不至于贴着边

const LONG_PRESS_MS = 250;
const MOVE_TOLERANCE = 8;       // 手指挪超过这么多像素就算"在滚"

const C = {
  bg: '#0d1017',
  ruler: '#101520',
  rulerText: '#96a0b8',
  rulerLine: 'rgba(255,255,255,0.16)',
  grid: 'rgba(255,255,255,0.065)',      // 轨道区里的竖向网格线
  laneA: 'rgba(255,255,255,0.028)',
  laneB: 'rgba(255,255,255,0.072)',
  laneLine: 'rgba(255,255,255,0.10)',
  head: '#161a24',
  headText: '#96a0b8',
  headEdge: 'rgba(255,255,255,0.10)',
  playhead: '#ff5c5c',
  sel: '#46ebbe',
  noteText: '#0d1017',
  // 音区配色（跟 `theme.ZONE_COLORS` 一致）
  zones: [
    'rgba(120,200,255,0.95)',
    'rgba(140,235,190,0.95)',
    'rgba(255,190,120,0.95)',
    'rgba(240,150,220,0.95)',
  ],
};

export class TimelineEditor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.lanes = DEFAULT_LANES;
    this.pxPerSec = 90;
    this.sel = null;
    this.dpr = 1;
    this._size = '';
    this._headKey = '';
    this.contentW = 0;
    this.holdTimer = 0;
    this.dragging = false;      // 正在拖方块
    this.onChanged = null;      // 拖完之后通知外面（写回文本）
  }

  /**
   * 按**内容**尺寸调整画布（不是视口尺寸）——
   * 宽 = 整首曲子的时长 × 缩放 + 右边留白，高 = 视口高度。
   * 这样外面那个滚动容器才有东西可滚。
   */
  layout(viewW, viewH, totalSec) {
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const needW = Math.max(viewW, Math.ceil(totalSec * this.pxPerSec) + PAD_RIGHT);
    const key = needW + 'x' + viewH + '@' + dpr;
    if (key !== this._size) {
      this.canvas.width = Math.max(1, Math.round(needW * dpr));
      this.canvas.height = Math.max(1, Math.round(viewH * dpr));
      this.canvas.style.width = needW + 'px';
      this.canvas.style.height = viewH + 'px';
      this._size = key;
    }
    this.dpr = dpr;
    this.cssW = needW;
    this.cssH = viewH;
    this.contentW = needW;
    // 轨道行高按可用高度撑开 —— 视口高的时候别让下面空一大片。
    //   上限给到 120：这台琴就 4 行（见 `layout.PAD_GRID`），
    //   4 条轨道要把整块区域铺满才好看，而 90 以下又会显得太挤。
    const availH = Math.max(60, viewH - RULER_H);
    this.rowH = Math.max(34, Math.min(120, availH / this.lanes));
  }

  laneY(lane) { return RULER_H + lane * this.rowH; }
  get lanesBottom() { return RULER_H + this.lanes * this.rowH; }

  secToX(sec) { return HEADER_W + sec * this.pxPerSec; }
  xToSec(x) { return Math.max(0, (x - HEADER_W) / this.pxPerSec); }

  laneAt(y) {
    if (y < RULER_H || y >= this.lanesBottom) return null;
    const i = Math.floor((y - RULER_H) / this.rowH);
    return i >= 0 && i < this.lanes ? i : null;
  }

  /** 命中哪个方块。x 是**画布内部坐标**（外面已经加过滚动偏移了）。 */
  itemAt(items, x, y) {
    const lane = this.laneAt(y);
    if (lane === null) return null;
    // 从后往前，后画的在上面
    for (let i = items.length - 1; i >= 0; i--) {
      const g = this.geom(items, i);
      if (!g || g.lane !== lane) continue;
      if (x >= g.x && x <= g.x + NOTE_W) return i;
    }
    return null;
  }

  /** 方块的位置（等宽）。 */
  geom(items, i) {
    const it = items[i];
    if (!it || it.chord.is_rest) return null;
    const lane = laneOf(it.chord.pitches);
    if (lane === null) return null;
    return {
      x: this.secToX(it.start_sec),
      y: this.laneY(lane) + 4,
      w: NOTE_W,
      h: this.rowH - 8,
      lane,
      it,
    };
  }

  draw(items, { sec = 0, totalSec = 0, laneCount = 6 } = {}) {
    const ctx = this.ctx;
    const W = this.cssW;
    const Hh = this.cssH;
    this.lanes = laneCount;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, Hh);

    const bodyTop = RULER_H;
    const bodyH = Math.max(0, Hh - RULER_H);

    // ---- 轨道底纹（交替）+ 分隔线 ----
    for (let lane = 0; lane < this.lanes; lane++) {
      ctx.fillStyle = lane % 2 ? C.laneA : C.laneB;
      ctx.fillRect(HEADER_W, this.laneY(lane), W - HEADER_W, this.rowH);
    }
    ctx.strokeStyle = C.laneLine;
    ctx.lineWidth = 1;
    for (let k = 0; k <= this.lanes; k++) {
      const y = Math.round(RULER_H + k * this.rowH) + 0.5;
      ctx.beginPath();
      ctx.moveTo(HEADER_W, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }

    // ---- ★ 时间线：每 0.5 秒一条细线，整秒一条粗线，5 秒标数字 ★ ----
    const stepSec = this.pxPerSec >= 140 ? 0.25 : this.pxPerSec >= 60 ? 0.5 : 1;
    const maxT = Math.max(totalSec, (W - HEADER_W) / this.pxPerSec);
    ctx.font = '11px system-ui, sans-serif';
    for (let t = 0; t <= maxT + stepSec; t += stepSec) {
      const x = Math.round(this.secToX(t)) + 0.5;
      if (x < HEADER_W) continue;
      const isWhole = Math.abs(t - Math.round(t)) < 1e-6;
      const isFive = isWhole && Math.round(t) % 5 === 0;
      if (isWhole || stepSec >= 1) {
        ctx.strokeStyle = isFive
          ? 'rgba(255,255,255,0.20)' : 'rgba(255,255,255,0.11)';
      } else {
        ctx.strokeStyle = C.grid;
      }
      ctx.beginPath();
      ctx.moveTo(x, bodyTop);
      ctx.lineTo(x, this.lanesBottom);
      ctx.stroke();
    }

    // ---- 方块（等宽）----
    for (let i = 0; i < items.length; i++) {
      const g = this.geom(items, i);
      if (!g) continue;
      if (g.x + NOTE_W < HEADER_W - 20 || g.x > W) continue;
      roundRect(ctx, g.x, g.y, g.w, g.h, 6);
      ctx.fillStyle = C.zones[zoneOf(g.it.chord.pitches)];
      ctx.fill();
      if (i === this.sel) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = C.sel;
        ctx.stroke();
      }
      const label = g.it.chord.pitches.join('&');
      ctx.font = '700 12px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      if (ctx.measureText(label).width + 6 < g.w) {
        ctx.fillStyle = C.noteText;
        ctx.fillText(label, g.x + g.w / 2, g.y + g.h / 2 + 0.5);
      }
    }

    // ---- 顶部标尺 ----
    ctx.fillStyle = C.ruler;
    ctx.fillRect(HEADER_W, 0, W - HEADER_W, RULER_H);
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const labelStep = niceLabelStep(this.pxPerSec);
    for (let t = 0; t <= maxT + labelStep; t += labelStep) {
      const x = this.secToX(t);
      if (x < HEADER_W - 20 || x > W + 20) continue;
      // 刻度小竖线
      ctx.strokeStyle = C.rulerLine;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, RULER_H - 7);
      ctx.lineTo(Math.round(x) + 0.5, RULER_H);
      ctx.stroke();
      ctx.fillStyle = C.rulerText;
      ctx.fillText(fmtSec(t), x, RULER_H / 2 - 1);
    }
    ctx.strokeStyle = C.rulerLine;
    ctx.beginPath();
    ctx.moveTo(HEADER_W, RULER_H + 0.5);
    ctx.lineTo(W, RULER_H + 0.5);
    ctx.stroke();

    // ---- 播放头（贯穿标尺 + 轨道）----
    if (sec >= 0) {
      const px = Math.round(this.secToX(sec)) + 0.5;
      if (px >= HEADER_W - 1) {
        ctx.strokeStyle = C.playhead;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(px, 0);
        ctx.lineTo(px, this.lanesBottom);
        ctx.stroke();
        ctx.fillStyle = C.playhead;
        ctx.beginPath();
        ctx.moveTo(px - 6, 0);
        ctx.lineTo(px + 6, 0);
        ctx.lineTo(px, 10);
        ctx.closePath();
        ctx.fill();
      }
    }

    // ---- 左侧轨道头**不在这儿画** ----
    //   它得固定住，而这张画布是跟着滚的（画上去一滑就跑了）。
    //   见 `drawHeads()` —— 那是压在滚动层上面的另一张画布。

    // 轨道区以下的部分抹平（视口比轨道高时别留条纹）
    if (this.lanesBottom < Hh) {
      ctx.fillStyle = C.bg;
      ctx.fillRect(0, this.lanesBottom, W, Hh - this.lanesBottom);
    }
  }

  /**
   * 画左边那条**固定不动**的轨道头。
   *
   * ★ 为什么单独一张画布 ★
   *   第一版把它画在滚动画布的最左边 —— 一滑就跟着跑，白搭。
   *   现在它压在滚动层上面（CSS `position: absolute`），纹丝不动。
   *
   * ★ 显示的是**音名**，不是「轨 N」★
   *   "轨 4" 对用户没有任何意义，还得去数。这一轨是哪几个键
   *   （`5 6 7 8`）才是能直接对上游戏里那台琴的东西。
   *   每行只显示第一个 —— 48px 宽放不下四个。
   */
  drawHeads(canvas, viewH) {
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const w = HEADER_W;
    const key = w + 'x' + viewH + '@' + dpr + '@' + this.lanes
      + '@' + Math.round(this.rowH);
    if (key !== this._headKey) {
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(viewH * dpr));
      canvas.style.width = w + 'px';
      canvas.style.height = viewH + 'px';
      this._headKey = key;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, viewH);

    ctx.strokeStyle = C.headEdge;
    ctx.lineWidth = 1;
    ctx.font = '600 13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // 顶部标尺那一格
    ctx.fillStyle = C.head;
    ctx.fillRect(0, 0, w, RULER_H);
    ctx.beginPath();
    ctx.moveTo(0, RULER_H + 0.5);
    ctx.lineTo(w, RULER_H + 0.5);
    ctx.stroke();

    for (let lane = 0; lane < this.lanes; lane++) {
      const y = this.laneY(lane);
      ctx.fillStyle = C.head;
      ctx.fillRect(0, y, w, this.rowH);
      // 左边一道竖杠（PR 的 track header 一进来先看到的就是它）
      ctx.fillStyle = 'rgba(70,201,168,0.6)';
      ctx.fillRect(5, y + 7, 3, this.rowH - 14);
      // 这一轨最低那个键的音名
      const row = 3 - lane;
      if (row >= 0 && row < PAD_GRID.length) {
        ctx.fillStyle = C.headText;
        ctx.fillText(PAD_GRID[row][0], w / 2 + 4, y + this.rowH / 2);
      }
    }
    if (this.lanesBottom < viewH) {
      ctx.fillStyle = C.head;
      ctx.fillRect(0, this.lanesBottom, w, viewH - this.lanesBottom);
    }
    ctx.beginPath();
    ctx.moveTo(w - 0.5, 0);
    ctx.lineTo(w - 0.5, viewH);
    ctx.stroke();
  }
}

// ---- 坐标换算的小工具 ----

function niceLabelStep(pxPerSec) {
  const want = 90;                      // 每个标签大概隔多少像素
  const cands = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
  const ideal = want / pxPerSec;
  for (const c of cands) if (c >= ideal) return c;
  return 600;
}

function fmtSec(t) {
  if (t < 60) {
    return (Math.abs(t - Math.round(t)) < 1e-6) ? (t + 's') : (t.toFixed(2) + 's');
  }
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return m + ':' + String(Math.round(s)).padStart(2, '0');
}

/** 音高 -> 轨道号（高的在上）。 */
function laneOf(pitches) {
  let best = null;
  for (const p of pitches) {
    const c = pitch_to_cell(p);
    if (!c) continue;
    const lane = 3 - c[0];
    if (best === null || lane < best) best = lane;
  }
  return best;
}

/** 音区序号（0 = 中音区，往上依次 +1）。 */
function zoneOf(pitches) {
  let z = 0;
  for (const p of pitches) {
    const c = pitch_to_cell(p);
    if (c) z = Math.max(z, c[0] >= 2 ? c[0] - 1 : 0);
  }
  return Math.max(0, Math.min(3, z));
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, Math.min(w, h) / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export { LONG_PRESS_MS, MOVE_TOLERANCE };
