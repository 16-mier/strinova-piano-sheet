// 练琴页底下那条「进度时间轴」—— 演奏的时候用来看进度、也能点着跳转。
//
// ★ 跟制谱页那条 (`tledit.js`) 的分工 ★
//   这一条是**只读的导航条**：整首曲子缩成一条，红针指到哪儿就是播到哪儿，
//   点一下就能跳过去。它不滚动、不编辑 —— 演奏时手是空的，不能再去拨它。
//
//   制谱页那条是**编辑器**：可以滚、可以拖方块。
//   两个的观感保持一致（同一种音符配色、同一条红针），但职责完全不同。
//
// ★ 为什么音符画成小竖条而不是方块 ★
//   整首曲子（可能几分钟）压进一个屏幕宽的条里，一个音只摊到几个像素。
//   画方块的话全都糊成一片；竖条反而能看出"这一段密不密、高音还是低音"——
//   那正是演奏时想知道的东西。

import { pitch_to_cell } from './layout.js';

const H = 62;            // 整条的高度
const LANE_H = 40;       // 上面 40px 放音符
const TICK_H = 22;       // 下面 22px 放时间刻度
const PAD_X = 8;         // 左右留白

const C = {
  bg: 'rgba(13,16,23,0.92)',
  edge: 'rgba(58,69,96,0.9)',
  laneLine: 'rgba(255,255,255,0.07)',
  tick: 'rgba(255,255,255,0.16)',
  tickText: '#7b8599',
  playhead: '#ff5c5c',
  // 音区配色（跟 `theme.ZONE_COLORS` / `tledit.js` 同一套）
  zones: [
    'rgba(120,200,255,0.95)',
    'rgba(140,235,190,0.95)',
    'rgba(255,190,120,0.95)',
    'rgba(240,150,220,0.95)',
  ],
  played: 'rgba(70,201,168,0.30)',   // 已经播过的一段（淡淡地标出来）
};

export class TimelineStrip {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.dpr = 1;
    this._size = '';
    this.onSeek = null;      // 点一下回调 (秒)
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
  }

  /** 时间 -> 横坐标。 */
  secToX(sec, total) {
    const span = this.cssW - PAD_X * 2;
    if (!total || total <= 0) return PAD_X;
    return PAD_X + (Math.max(0, Math.min(total, sec)) / total) * span;
  }

  /** 横坐标 -> 时间（点击跳转用）。 */
  xToSec(x, total) {
    const span = this.cssW - PAD_X * 2;
    if (span <= 0) return 0;
    return Math.max(0, Math.min(total, ((x - PAD_X) / span) * total));
  }

  /**
   * @param {Timeline|null} timeline
   * @param {number} sec 当前播放位置
   */
  draw(timeline, sec) {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const W = this.cssW;
    const Hh = this.cssH;

    ctx.clearRect(0, 0, W, Hh);
    roundRect(ctx, 0.5, 0.5, W - 1, Hh - 1, 10);
    ctx.fillStyle = C.bg;
    ctx.fill();
    ctx.strokeStyle = C.edge;
    ctx.lineWidth = 1;
    ctx.stroke();

    const total = timeline ? timeline.total_sec : 0;
    const items = timeline ? timeline.items : [];

    // ---- 音符区：竖条 ----
    const laneTop = 6;
    const laneH = LANE_H - 8;
    // 几条淡淡的横向参考线（高/中/低）
    ctx.strokeStyle = C.laneLine;
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = Math.round(laneTop + (laneH / 4) * i) + 0.5;
      ctx.beginPath();
      ctx.moveTo(PAD_X, y);
      ctx.lineTo(W - PAD_X, y);
      ctx.stroke();
    }

    if (items.length && total > 0) {
      // ★ 已经播过的一段淡淡染个色 ★
      //   一眼看出"走到哪儿了"，不用去数红针的位置。
      const px = this.secToX(sec, total);
      ctx.fillStyle = C.played;
      ctx.fillRect(PAD_X, laneTop, Math.max(0, px - PAD_X), laneH);

      // ★ 竖条要够粗才看得见 ★
      //   一个音在这条上只摊到几个像素，2px 的线在手机上基本等于没有。
      const barW = items.length > 120 ? 3 : items.length > 60 ? 4 : 5;
      for (const it of items) {
        if (it.chord.is_rest) continue;
        const x = this.secToX(it.start_sec, total);
        // 纵向按音高：行 3（最高）画最上面
        const cell = pitchCell(it.chord.pitches[0]);
        if (!cell) continue;
        const t = 1 - cell[0] / 3;              // 0 = 最下（低音）
        const y = laneTop + t * (laneH - 14);
        ctx.fillStyle = C.zones[zoneOf(it.chord.pitches)];
        ctx.fillRect(Math.round(x - barW / 2), Math.round(y), barW, 14);
      }
    } else {
      ctx.fillStyle = C.tickText;
      ctx.font = '11px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('还没有载入谱面', W / 2, laneTop + laneH / 2);
    }

    // ---- 分隔线 ----
    ctx.strokeStyle = C.edge;
    ctx.beginPath();
    ctx.moveTo(0, LANE_H + 0.5);
    ctx.lineTo(W, LANE_H + 0.5);
    ctx.stroke();

    // ---- 时间刻度 ----
    ctx.font = '10px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    const step = niceStep(total);
    if (total > 0) {
      for (let t = 0; t <= total + 1e-6; t += step) {
        const x = this.secToX(t, total);
        if (x < PAD_X - 1 || x > W - PAD_X + 1) continue;
        ctx.strokeStyle = C.tick;
        ctx.beginPath();
        ctx.moveTo(Math.round(x) + 0.5, LANE_H);
        ctx.lineTo(Math.round(x) + 0.5, LANE_H + 4);
        ctx.stroke();
        ctx.fillStyle = C.tickText;
        ctx.fillText(fmtTick(t), x, LANE_H + TICK_H / 2 + 3);
      }
    }

    // ---- 播放头（最后画，压在最上面）----
    if (total > 0) {
      const px = Math.round(this.secToX(sec, total)) + 0.5;
      ctx.strokeStyle = C.playhead;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(px, 2);
      ctx.lineTo(px, Hh - 2);
      ctx.stroke();
      // 顶上一个小三角把手
      ctx.fillStyle = C.playhead;
      ctx.beginPath();
      ctx.moveTo(px - 5, 2);
      ctx.lineTo(px + 5, 2);
      ctx.lineTo(px, 9);
      ctx.closePath();
      ctx.fill();
    }
  }
}

/** 刻度间隔 —— 让整条上大概有 4~6 个刻度，别挤成一团。 */
function niceStep(total) {
  if (!total || total <= 0) return 1;
  const raw = total / 5;
  const cands = [1, 2, 5, 10, 15, 30, 60, 120, 300];
  for (const c of cands) if (raw <= c) return c;
  return 600;
}

function fmtTick(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  return m > 0 ? (m + ':' + String(s).padStart(2, '0')) : (s + 's');
}

// ---- 键位 / 音区（跟 tledit.js 同一套规则，都从 layout.js 取）----

function pitchCell(pitch) {
  return pitch_to_cell(pitch);
}

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
