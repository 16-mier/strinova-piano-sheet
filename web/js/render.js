// 画那 4×4 的格子（练琴页的大网格 / 制谱页的打击垫共用一套）。
//
// ★ 视觉规则是从桌面版 `ui/views.py::GridView` 搬过来的 ★
//   最要紧的一条：**深底不动，让轮廓去表达事件**。
//   用户明确说过「已经被点过的不需要浅色了」——
//   所以按下也好、悬停也好、该弹也好，全是改边框，
//   格子底色永远待在深色区间里。
//
// ★ 高 DPI ★
//   手机屏的 devicePixelRatio 普遍是 2~3。不按它放大 canvas 的
//   实际像素、只在 CSS 里拉伸的话，格子和字全都是糊的。

import * as L from './layout.js';

// 桌面版 `ui/theme.py` 里的那几个数
const GAP = 6;

const C = {
  cell: 'rgba(46,52,70,0.78)',
  cellEdge: 'rgba(98,108,140,0.84)',

  // 当前该弹的那个：深橄榄 + 亮黄粗边（跟桌面版一致）
  active: 'rgba(96,108,74,0.92)',
  activeEdge: '#baca96',

  // 预告格：从"下一个"往后越来越暗（桌面版 `theme.PREVIEW` 五档）
  preview: [
    'rgba(78,90,124,0.89)',
    'rgba(66,77,107,0.86)',
    'rgba(58,67,94,0.84)',
    'rgba(51,59,82,0.81)',
    'rgba(46,52,70,0.78)',
  ],

  text: '#eaeef8',
  textDim: '#9aa2b6',
  pad: 'rgba(154,162,182,0.62)',

  press: '#ffd64a',       // 该弹那个的边
  hit: '#46ebbe',         // "你按了"的反馈色
  ring: 'rgba(255,206,48,1)',

  badgeBg: 'rgba(255,206,48,0.92)',
  badgeText: '#1c1400',
};

// 收缩圆圈的几个常数（桌面版 `views.py` 顶部那组）
const LEAD = 1.2;          // 圈统一在音前多久"挂出来"
const LEAD_MIN = 0.35;     // 两个音挨太近时，圈至少给这么久
const RING_MAX_FRAC = 0.45; // 圈最大半径 = 格子边长的一半 × 这个

const RADIUS_RATIO = 0.14;  // 圆角 = 格子边长 × 这个

/** 一个格子该长什么样，跟桌面版 `_cell_style()` 对应的几个分支。 */
export class StageRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.cell = 0;
    this.ox = 0;
    this.oy = 0;
    this.dpr = 1;
    this._lastSize = '';
  }

  /** 按容器大小 + 设备像素比调整画布，并算好几何量。 */
  layout(cssW, cssH) {
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const key = cssW + 'x' + cssH + '@' + dpr;
    if (key !== this._lastSize) {
      this.canvas.width = Math.max(1, Math.round(cssW * dpr));
      this.canvas.height = Math.max(1, Math.round(cssH * dpr));
      this.canvas.style.width = cssW + 'px';
      this.canvas.style.height = cssH + 'px';
      this._lastSize = key;
    }
    this.dpr = dpr;
    const side = Math.min(cssW, cssH);
    this.cell = (side - 3 * GAP) / 4;
    this.ox = (cssW - side) / 2;
    this.oy = (cssH - side) / 2;
    this.cssW = cssW;
    this.cssH = cssH;
  }

  /** 格子 (row,col) 的矩形。**行 3 画在最上面**（跟游戏画面一致）。 */
  cellRect(row, col) {
    return {
      x: this.ox + col * (this.cell + GAP),
      y: this.oy + (3 - row) * (this.cell + GAP),
      w: this.cell,
      h: this.cell,
    };
  }

  /** 屏幕坐标 -> [row, col]，没落在格子上返回 null。 */
  cellAt(px, py) {
    for (let row = 0; row < 4; row++) {
      for (let col = 0; col < 4; col++) {
        const r = this.cellRect(row, col);
        if (px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h) {
          return [row, col];
        }
      }
    }
    return null;
  }

  /**
   * 画一帧。
   *
   * @param {object} s
   *   s.marks     Map<"row,col", {rank, count}>  rank 0 = 当前该弹的
   *   s.flash     Map<"row,col", {k}>            k = 亮度比例 0~1
   *   s.hover     [row,col] | null
   *   s.flashes   [{row,col,k}]   额外的高亮（"你按的"那一下）
   *   s.showPad   是否画 PAD 号
   *   s.rings     [{row,col,frac}] 收缩圆圈，frac 1->0
   *   s.dim       整体压暗（0~1，1 = 正常）
   */
  draw(s) {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);

    const marks = s.marks || new Map();
    const flash = s.flash || new Map();
    const rings = s.rings || [];

    for (let row = 0; row < 4; row++) {
      for (let col = 0; col < 4; col++) {
        const key = row + ',' + col;
        const mark = marks.get(key);
        const fl = flash.get(key);
        const hovered = s.hover && s.hover[0] === row && s.hover[1] === col;
        const r = this.cellRect(row, col);

        // ---- 底色 ----
        let fill = C.cell;
        let edge = C.cellEdge;
        let lw = Math.max(1.2, this.cell * 0.016);

        if (mark) {
          if (mark.rank === 0) {
            fill = C.active;
            edge = C.activeEdge;
            lw = Math.max(2.6, this.cell * 0.038);   // 当前格一圈亮黄粗边
          } else {
            // 越往后越暗（桌面版就是拿这五档"用颜色深浅读先后"）
            fill = C.preview[Math.min(mark.rank - 1, C.preview.length - 1)];
            edge = C.cellEdge;
          }
        }

        if (hovered && !fl) {
          edge = 'rgba(132,146,186,0.92)';
          lw = Math.max(1.8, this.cell * 0.024);
        }

        const rad = this.cell * RADIUS_RATIO;

        // 闪的时候往外扩一点点
        let rr = r;
        if (fl) {
          const grow = this.cell * 0.03;
          rr = { x: r.x - grow, y: r.y - grow, w: r.w + grow * 2, h: r.h + grow * 2 };
        }

        roundRect(ctx, rr.x, rr.y, rr.w, rr.h, rad);
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.lineWidth = lw;
        ctx.strokeStyle = edge;
        ctx.stroke();

        // ---- 收缩圆圈 ----
        for (const ring of rings) {
          if (ring.row !== row || ring.col !== col) continue;
          const cx = r.x + r.w / 2;
          const cy = r.y + r.h / 2;
          const maxR = this.cell * RING_MAX_FRAC;
          const radius = maxR * Math.max(0, Math.min(1, ring.frac));
          if (radius <= 0.5) continue;
          ctx.beginPath();
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
          ctx.lineWidth = Math.max(2, this.cell * 0.03);
          // ★ 越接近该按越烫 ★ —— 桌面版 16.74 那条：数字和圈一起变色
          const heat = 1 - Math.max(0, Math.min(1, ring.frac));
          ctx.strokeStyle = heat > 0.75 ? '#ff8a5c'
            : heat > 0.4 ? C.press : C.ring;
          ctx.globalAlpha = 0.9;
          ctx.stroke();
          ctx.globalAlpha = 1;
        }

        // ---- 按下闪光：**只描边，不填** ----
        //   （桌面版 16.75：拿填充盖住整格的话，底下的序号角标、
        //     音名全被压掉了 —— 那不叫闪烁，叫"换了个颜色"）
        if (fl) {
          const k = Math.max(0, Math.min(1, fl.k));
          const g = this.cell * 0.03;
          const box = { x: r.x - g, y: r.y - g, w: r.w + g * 2, h: r.h + g * 2 };
          roundRect(ctx, box.x, box.y, box.w, box.h, rad);
          ctx.lineWidth = Math.max(4, this.cell * 0.07);
          ctx.strokeStyle = `rgba(70,235,190,${(0.32 * k).toFixed(3)})`;
          ctx.stroke();
          roundRect(ctx, box.x, box.y, box.w, box.h, rad);
          ctx.lineWidth = Math.max(1.6, this.cell * 0.026);
          ctx.strokeStyle = `rgba(70,235,190,${(0.9 * k + 0.1).toFixed(3)})`;
          ctx.stroke();
        }

        // ---- 字 ----
        const pitch = L.cell_to_pitch(row, col);
        const bigFont = Math.max(13, this.cell * 0.30);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = C.text;
        ctx.font = `${mark && mark.rank === 0 ? '700 ' : '500 '}${bigFont}px system-ui, sans-serif`;

        const cx = r.x + r.w / 2;
        let cy = r.y + r.h / 2;
        if (s.showPad) cy -= this.cell * 0.06;
        ctx.fillText(pitch, cx, cy);

        if (s.showPad) {
          ctx.fillStyle = C.pad;
          const small = Math.max(9, this.cell * 0.17);
          ctx.font = `400 ${small}px system-ui, sans-serif`;
          ctx.fillText('PAD ' + L.pad_number(row, col), cx, r.y + r.h * 0.76);
        }

        // ---- 序号角标（"后面还有几个"）----
        if (mark && mark.rank > 0) {
          this._badge(ctx, r, String(mark.rank));
        }
        // ---- 连按次数 `×N` ----
        if (mark && mark.rank === 0 && mark.count > 1) {
          this._badge(ctx, r, '×' + mark.count, true);
        }
      }
    }
  }

  _badge(ctx, r, text, topLeft) {
    const h = Math.max(13, this.cell * 0.22);
    const pad = h * 0.34;
    ctx.font = `700 ${h * 0.72}px system-ui, sans-serif`;
    const w = ctx.measureText(text).width + pad * 2;
    const x = r.x + Math.max(3, this.cell * 0.055);
    const y = r.y + Math.max(3, this.cell * 0.055);
    roundRect(ctx, x, y, w, h, h * 0.34);
    ctx.fillStyle = C.badgeBg;
    ctx.fill();
    ctx.fillStyle = C.badgeText;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + w / 2, y + h / 2 + 0.5);
  }
}

/** 圆角矩形路径（老浏览器没有 ctx.roundRect）。 */
export function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, Math.min(w, h) / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.arcTo(x + w, y, x + w, y + rr, rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.arcTo(x + w, y + h, x + w - rr, y + h, rr);
  ctx.lineTo(x + rr, y + h);
  ctx.arcTo(x, y + h, x, y + h - rr, rr);
  ctx.lineTo(x, y + rr);
  ctx.arcTo(x, y, x + rr, y, rr);
  ctx.closePath();
}

/**
 * 从"当前时刻"算出一帧要画什么 —— 对应桌面版 `GridView._frame()`。
 *
 * `timed` 里每个元素是 `{row, col, start_sec, lead, count}`：
 *   · rank 0 是"现在该弹的"，往后 1、2、3…
 *   · `count` 是连按次数（同一个键连着出现几次）
 */
export function buildFrame(timeline, sec, previewCount) {
  const marks = new Map();
  const rings = [];

  if (!timeline || timeline.items.length === 0) return { marks, rings };

  const idx = Math.max(0, timeline.index_at(sec));
  const items = timeline.items;

  // ★ "同一个键连着出现"要数出来（桌面版 `repeat_run`）★
  //   用户：「如果是连续点俩下在 1 下面写一个 ×2」。
  //   注意是**相邻重复**，不是"总共出现几次" —— `5 3 5` 不该标 ×2。
  const countRun = (i, pitch) => {
    let n = 0;
    for (let k = i; k < items.length; k++) {
      const c = items[k].chord;
      if (c.is_rest || !c.pitches.includes(pitch)) break;
      n += 1;
    }
    return n;
  };

  for (let n = 0; n < previewCount; n++) {
    const i = idx + n;
    if (i >= items.length) break;
    const it = items[i];
    if (it.chord.is_rest) continue;

    // `lead` = 这个音跟上一个音隔了多久，夹在 [LEAD_MIN, LEAD]
    let lead = LEAD;
    if (i > 0) {
      const d = it.start_sec - items[i - 1].start_sec;
      if (d > 0) lead = Math.max(LEAD_MIN, Math.min(LEAD, d));
    }

    for (const pitch of it.chord.pitches) {
      const cell = L.pitch_to_cell(pitch);
      if (!cell) continue;                       // 琴上没这个音
      const key = cell[0] + ',' + cell[1];
      // 同一个格子已经有更靠前的序号就别覆盖（和弦里两个音落同一格时）
      if (marks.has(key)) continue;
      marks.set(key, {
        rank: n,
        count: n === 0 ? countRun(i, pitch) : 1,
      });

      // 收缩圈：只在"还没到点"的时候画，缩完就没了
      if (n === 0) {
        const left = it.start_sec - sec;
        if (left > -0.12 && left <= LEAD) {
          const frac = Math.max(0, Math.min(1, left / lead));
          rings.push({ row: cell[0], col: cell[1], frac });
        }
      }
    }
  }

  return { marks, rings };
}
