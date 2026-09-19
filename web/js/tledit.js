// 时间轴编辑器 —— 手机上那条可横向滚动、能拖音符的轨道区。
//
// 视觉照桌面版 `ui/timeline_edit.py` 那套（那是按 Premiere 的分区做的）：
//   顶部标尺 + 左侧轨道头 + 轨道斑马纹 + 圆角音符块。
// 手机上做了三处让步：轨道矮一点、轨道头窄一点、默认视野短一点。
//
// 交互设计（手机没有右键、没有 Shift）：
//   · 单指横向拖   → 滚时间轴
//   · 点音符       → 选中
//   · 按住音符拖   → 改它的时间（左右）和轨道（上下）
//   · 点空白       → 取消选中

import { pitch_to_cell } from './layout.js';

const RULER_H = 26;
const ROW_H = 34;
const HEADER_W = 46;
export const DEFAULT_LANES = 6;
export const LANES_MAX = 12;

const C = {
  bg: '#0d1017',
  ruler: '#101520',
  rulerEdge: 'rgba(255,255,255,0.18)',
  rulerText: '#96a0b8',
  laneA: 'rgba(255,255,255,0.031)',
  laneB: 'rgba(255,255,255,0.078)',
  laneLine: 'rgba(255,255,255,0.10)',
  head: '#161a24',
  headEdge: 'rgba(255,255,255,0.10)',
  headText: '#96a0b8',
  playhead: '#ff5c5c',
  sel: '#46ebbe',
  // 音区配色（跟桌面版 `theme.ZONE_COLORS` 一致）
  zones: [
    'rgba(120,200,255,0.92)',   // 中音区
    'rgba(140,235,190,0.92)',   // 中音高段
    'rgba(255,190,120,0.92)',   // 高音区
    'rgba(240,150,220,0.92)',   // 倍高音
  ],
  noteText: '#0d1017',
};

export class TimelineEditor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.lanes = DEFAULT_LANES;
    this.pxPerSec = 90;
    this.scrollX = 0;      // 已经滚过去多少像素
    this.dpr = 1;
    this.sel = null;       // 选中的音符下标
    this._size = '';
  }

  layout(cssW, cssH) {
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const key = cssW + 'x' + cssH + '@' + dpr;
    if (key !== this._size) {
      this.canvas.width = Math.max(1, Math.round(cssW * dpr));
      this.canvas.height = Math.max(1, Math.round(cssH * dpr));
      this.canvas.style.width = cssW + 'px';
      this.canvas.style.height = cssH + 'px';
      this._size = key;
    }
    this.dpr = dpr;
    this.cssW = cssW;
    this.cssH = cssH;
    this.trackW = Math.max(10, cssW - HEADER_W);
  }

  laneY(lane) { return RULER_H + lane * ROW_H; }
  get lanesBottom() { return RULER_H + this.lanes * ROW_H; }

  secToX(sec) { return HEADER_W + sec * this.pxPerSec - this.scrollX; }
  xToSec(x) { return (x - HEADER_W + this.scrollX) / this.pxPerSec; }

  /** 纵向 -> 轨道号；不在轨道区就返回 null。 */
  laneAt(y) {
    if (y < RULER_H || y >= this.lanesBottom) return null;
    const i = Math.floor((y - RULER_H) / ROW_H);
    return i >= 0 && i < this.lanes ? i : null;
  }

  /** 命中哪个音符 —— 从后往前找（后画的在上面）。 */
  itemAt(items, x, y) {
    const lane = this.laneAt(y);
    if (lane === null) return null;
    for (let i = items.length - 1; i >= 0; i--) {
      const g = this.geom(items, i);
      if (!g || g.lane !== lane) continue;
      if (x >= g.x && x <= g.x + g.w && y >= g.y + 3 && y <= g.y + ROW_H - 3) {
        return i;
      }
    }
    return null;
  }

  /** 音符在屏幕上的方块。宽度铺到下一个音（桌面版就是"自动铺满"的规矩）。 */
  geom(items, i) {
    const it = items[i];
    if (!it || it.chord.is_rest) return null;
    const lane = laneOf(it.chord.pitches);
    if (lane === null) return null;
    const x = this.secToX(it.start_sec);
    const next = items[i + 1];
    const endSec = next ? Math.max(it.start_sec + 0.08, next.start_sec)
                        : it.start_sec + 0.5;
    const w = Math.max(12, (endSec - it.start_sec) * this.pxPerSec - 2);
    return { x, y: this.laneY(lane) + 3, w, h: ROW_H - 6, lane, it };
  }

  draw(items, { sec = 0, totalSec = 0, laneCount = 6 } = {}) {
    const ctx = this.ctx;
    const W = this.cssW;
    const H = this.cssH;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, W, H);

    this.lanes = laneCount;

    // ---- 轨道底纹（交替深浅）+ 分隔线 ----
    for (let lane = 0; lane < this.lanes; lane++) {
      ctx.fillStyle = lane % 2 ? C.laneA : C.laneB;
      ctx.fillRect(HEADER_W, this.laneY(lane), this.trackW, ROW_H);
    }
    ctx.strokeStyle = C.laneLine;
    ctx.lineWidth = 1;
    for (let k = 0; k <= this.lanes; k++) {
      const y = Math.round(RULER_H + k * ROW_H) + 0.5;
      ctx.beginPath();
      ctx.moveTo(HEADER_W, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }

    // ---- 秒刻度线（每整秒一条，每 5 秒标数字）----
    const t0 = Math.max(0, this.xToSec(HEADER_W));
    const t1 = this.xToSec(W);
    ctx.font = '11px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    for (let s = Math.floor(t0); s <= Math.ceil(t1); s++) {
      if (s < 0) continue;
      const x = Math.round(this.secToX(s)) + 0.5;
      if (x < HEADER_W) continue;
      ctx.strokeStyle = s % 5 === 0
        ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.09)';
      ctx.beginPath();
      ctx.moveTo(x, RULER_H);
      ctx.lineTo(x, this.lanesBottom);
      ctx.stroke();
    }

    // ---- 音符块 ----
    for (let i = 0; i < items.length; i++) {
      const g = this.geom(items, i);
      if (!g) continue;
      if (g.x + g.w < HEADER_W || g.x > W) continue;   // 视野外
      const zone = zoneOf(g.it.chord.pitches);
      roundRect(ctx, g.x, g.y, g.w, g.h, Math.min(6, g.h * 0.28));
      ctx.fillStyle = C.zones[zone];
      ctx.fill();
      if (i === this.sel) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = C.sel;
        ctx.stroke();
      }
      // 音名（方块够宽才画，不然糊成一团）
      const label = g.it.chord.pitches.join('&');
      ctx.font = '700 11px system-ui, sans-serif';
      const tw = ctx.measureText(label).width;
      if (tw + 8 < g.w) {
        ctx.fillStyle = C.noteText;
        ctx.textAlign = 'center';
        ctx.fillText(label, g.x + g.w / 2, g.y + g.h / 2 + 0.5);
      }
    }

    // ---- 顶部标尺 ----
    ctx.fillStyle = C.ruler;
    ctx.fillRect(HEADER_W, 0, W - HEADER_W, RULER_H);
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'center';
    for (let s = Math.floor(t0 / 5) * 5; s <= Math.ceil(t1); s += 5) {
      if (s < 0) continue;
      const x = this.secToX(s);
      if (x < HEADER_W - 20 || x > W + 20) continue;
      ctx.fillStyle = C.rulerText;
      ctx.fillText(fmtLabel(s), x, RULER_H / 2 + 0.5);
    }
    ctx.strokeStyle = C.rulerEdge;
    ctx.beginPath();
    ctx.moveTo(HEADER_W, RULER_H + 0.5);
    ctx.lineTo(W, RULER_H + 0.5);
    ctx.stroke();

    // ---- 播放头（贯穿标尺 + 轨道）----
    const phx = this.secToX(sec);
    if (phx >= HEADER_W - 1 && phx <= W + 1) {
      ctx.strokeStyle = C.playhead;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(Math.round(phx) + 0.5, 0);
      ctx.lineTo(Math.round(phx) + 0.5, this.lanesBottom);
      ctx.stroke();
      // 标尺里那个把手（PR 那种）
      ctx.fillStyle = C.playhead;
      ctx.beginPath();
      ctx.moveTo(phx - 5, 2);
      ctx.lineTo(phx + 5, 2);
      ctx.lineTo(phx, 10);
      ctx.closePath();
      ctx.fill();
    }

    // ---- 左侧轨道头 ----
    ctx.fillStyle = C.head;
    ctx.fillRect(0, 0, HEADER_W, H);
    ctx.font = '600 11px system-ui, sans-serif';
    ctx.fillStyle = C.headText;
    ctx.textAlign = 'center';
    for (let lane = 0; lane < this.lanes; lane++) {
      const y = this.laneY(lane);
      // 每条轨道左边一道竖杠（PR 的轨道头一进来先看到的就是它）
      ctx.fillStyle = 'rgba(70,201,168,0.55)';
      ctx.fillRect(4, y + 6, 3, ROW_H - 12);
      ctx.fillStyle = C.headText;
      ctx.fillText('轨 ' + (this.lanes - lane), HEADER_W / 2 + 3, y + ROW_H / 2);
    }
    ctx.strokeStyle = C.headEdge;
    ctx.beginPath();
    ctx.moveTo(HEADER_W + 0.5, 0);
    ctx.lineTo(HEADER_W + 0.5, H);
    ctx.stroke();
  }

  /** 把视野滚到某个时刻（跟随播放头用）。 */
  scrollTo(sec) {
    const x = HEADER_W + sec * this.pxPerSec - this.scrollX;
    if (x > this.cssW - 60) this.scrollX = sec * this.pxPerSec - (this.cssW - HEADER_W) * 0.35;
    else if (x < HEADER_W) this.scrollX = Math.max(0, sec * this.pxPerSec - 40);
  }
}

/** 音高 -> 轨道号（高的在上）。返回 null 表示琴上没这个音。 */
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

function fmtLabel(sec) {
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + ':' + String(s).padStart(2, '0');
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
