// 卡丘琴谱器 手机版 —— 主控。
//
// 三页：练琴（浮窗那套视觉 + 播放 + 三种模式）、制谱、曲库。
//
// ★ 跟桌面版的关系 ★
//   解析 / 时间轴 / 布局是**同一套逻辑的移植**（`tools/port_check_*.py`
//   拿真实用例逐字段对拍过，两边结果完全一致）。
//   谱面也是同一种 .txt 格式，所以两边互通不需要任何转换。
//
// ★ 时钟为什么用 performance.now() ★
//   桌面版那边踩过坑：用 setInterval 累加会漂（每次回调的误差会攒起来），
//   弹到后面就越差越多。这里跟桌面版一样走"真实经过的时间"。

import { parse } from './parser.js';
import { Timeline, timeline_from_notes } from './timeline.js';
import * as L from './layout.js';
import { StageRenderer, buildFrame } from './render.js';
import { NotePlayer } from './audio.js';
import { SheetStore, downloadText, pickTextFile, stripExt, copyText } from './store.js';
import { TimelineEditor } from './tledit.js';

// ---------------------------------------------------------------------------
// 内置示例（第一次打开时塞进曲库）
// ---------------------------------------------------------------------------

// 跟电脑版 `sheets/demo.txt` 是同一份 —— 两边看到的第一个谱面一样
const DEMO_SHEET = `小星星
#100#
1 1 5 5 6 6 5-
4 4 3 3 2 2 1-
5 5 4 4 3 3 2-
5 5 4 4 3 3 2-
1 1 5 5 6 6 5-
4 4 3 3 2 2 1-`;

// 时间轴格式（`秒:音高`）—— 位置自己说了算，可以重叠
const DEMO_FREE = `示例 · 时间轴
#120#
0:1 0.5:1 1:5 1.5:5 2:6 2.5:6 3:5 3.5:5
0:1' 2:1' 4:1'' 4:3'`;

// ---------------------------------------------------------------------------
// 全局状态
// ---------------------------------------------------------------------------

const S = {
  store: new SheetStore(),
  player: new NotePlayer(),

  sheetName: '',
  sheetText: '',
  timeline: null,

  sec: 0,
  playing: false,
  lastTick: 0,

  karaoke: false,     // 跟打：播放不放原声，点格子才出声
  tap: false,         // 可按：浮窗的格子能点着发声
  train: false,       // 训练：按音符顺序点，点对才走下一个
  trainSeq: [],
  trainI: 0,
  trainT0: 0,
  trainGap: 0.5,

  previewCount: 5,
  volume: 0.75,

  flash: new Map(),   // "row,col" -> {expire, dur}
  hover: null,
  padHover: null,
  trainFlash: null,
};

let stage = null;       // StageRenderer（练琴页）
let pad = null;         // StageRenderer（制谱页的打击垫）
let tled = null;        // TimelineEditor（制谱页的时间轴）
let editingName = '';   // 制谱器正在编辑哪一份

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function fmtTime(sec) {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return String(m).padStart(2, '0') + ':' + rest.toFixed(1).padStart(4, '0');
}

let toastTimer = 0;
function toast(text, ms = 2200) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('is-on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('is-on'), ms);
}

function flashCell(row, col, dur = 0.25) {
  S.flash.set(row + ',' + col, { expire: performance.now() / 1000 + dur, dur });
}

/** 把当前的闪光状态转成渲染层要的比例（越接近 0 越暗）。 */
function flashNow() {
  const now = performance.now() / 1000;
  const out = new Map();
  for (const [k, v] of S.flash) {
    const left = v.expire - now;
    if (left <= 0) {
      S.flash.delete(k);
      continue;
    }
    out.set(k, { k: Math.min(1, left / v.dur) });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 载入 / 保存
// ---------------------------------------------------------------------------

function loadSheet(name, { silent = false } = {}) {
  const text = S.store.get(name);
  if (text === undefined) return false;
  S.sheetName = name;
  S.sheetText = text;
  S.store.current = name;
  S.timeline = new Timeline(parse(text));
  S.sec = 0;
  S.playing = false;
  stopTrain();
  buildTrainSeq();
  refreshHeader();
  refreshList();
  if (!silent) toast('已载入 ' + name);
  return true;
}

function refreshHeader() {
  $('song-name').textContent = S.sheetName || '没有载入谱面';
  const tl = S.timeline;
  $('song-meta').textContent = tl ? tl.stats() : '';
  $('time-total').textContent = fmtTime(tl ? tl.total_sec : 0);
}

function saveCurrentText() {
  if (!S.sheetName) return;
  S.store.put(S.sheetName, S.sheetText);
}

// ---------------------------------------------------------------------------
// 播放时钟
// ---------------------------------------------------------------------------

function play() {
  if (!S.timeline || !S.timeline.items.length) {
    toast('还没有载入谱面');
    return;
  }
  S.player.unlock();
  if (S.sec >= S.timeline.total_sec - 1e-6) S.sec = 0;
  S.playing = true;
  S.lastTick = performance.now();
  S._soundPtr = nextIndexAt(S.sec);
  $('btn-play').textContent = '❚❚';
}

function pause() {
  S.playing = false;
  $('btn-play').textContent = '▶';
}

function stop() {
  S.playing = false;
  $('btn-play').textContent = '▶';
  S.sec = 0;
  updateProgressUI();
}

function home() {
  S.sec = 0;
  S._soundPtr = nextIndexAt(0);
  updateProgressUI();
}

function nextIndexAt(sec) {
  if (!S.timeline) return 0;
  return Math.max(0, S.timeline.index_at(sec) + 1);
}

function tick(now) {
  const dt = Math.min(0.1, (now - S.lastTick) / 1000);
  S.lastTick = now;

  if (S.playing && S.timeline) {
    S.sec += dt;

    // ★ 「播放声音」和「跟打」是互斥的 ★
    //   跟打开着就不放原声（那是"你点才出声"），跟桌面版一致。
    if (!S.karaoke) fireSounds();
    else S._soundPtr = nextIndexAt(S.sec);

    if (S.sec >= S.timeline.total_sec) {
      S.sec = S.timeline.total_sec;
      pause();
    }
    updateProgressUI();
  }

  drawStage();
  drawPad();
  drawTimeline();
  requestAnimationFrame(tick);
}

/**
 * 画制谱页那条时间轴。
 *
 * ★ 看不见的时候不画 ★
 *   它在另一个标签页里（`display: none`），`clientWidth` 是 0 ——
 *   这时候画等于白算，而且算出来的几何量全是垃圾。
 */
function drawTimeline() {
  if (!tled) return;
  const cv = $('tl-canvas');
  if (!cv.clientWidth || !cv.clientHeight) return;
  if (cv.clientWidth !== tled.cssW || cv.clientHeight !== tled.cssH) {
    fitCanvas(cv, tled);
  }
  const items = S.timeline ? S.timeline.items : [];
  tled.draw(items, {
    sec: S.sec,
    totalSec: S.timeline ? S.timeline.total_sec : 0,
    laneCount: S.timeline && S.timeline.items.length > 60 ? 12 : 6,
  });
}

/** 把这一小段时间里"该响"的音放出来。 */
function fireSounds() {
  if (!S.timeline) return;
  if (S._soundPtr === undefined) S._soundPtr = nextIndexAt(S.sec - 0.05);
  const items = S.timeline.items;
  while (S._soundPtr < items.length && items[S._soundPtr].start_sec <= S.sec) {
    for (const p of items[S._soundPtr].chord.pitches) S.player.play(p);
    S._soundPtr += 1;
  }
}

// ---------------------------------------------------------------------------
// 画面
// ---------------------------------------------------------------------------

function fitCanvas(canvas, renderer) {
  // ★ 量的是**父容器**，不是画布自己 ★
  //   第一版让 canvas 当普通 flex item：它会把自己的固有尺寸
  //   （`canvas.width` 那个位图像素宽度）反馈给父容器 —— 父容器被撑大
  //   → 下一帧量到更大的值 → 格子越画越宽，右边那一列被推出屏幕。
  //   现在 canvas 是绝对定位（见 CSS），不参与布局，量的就是真实尺寸。
  const wrap = canvas.parentElement;
  const w = Math.max(40, wrap.clientWidth);
  const h = Math.max(40, wrap.clientHeight);
  renderer.layout(w, h);
}

function drawStage() {
  if (!stage) return;
  const cv = $('stage');
  if (cv.clientWidth !== stage.cssW || cv.clientHeight !== stage.cssH) {
    fitCanvas(cv, stage);
  }

  const flash = flashNow();
  let marks, rings;

  if (S.train) {
    const fr = trainFrame();
    marks = fr.marks;
    rings = fr.rings;
  } else {
    const fr = buildFrame(S.timeline, S.sec, S.previewCount);
    marks = fr.marks;
    rings = fr.rings;
  }

  stage.draw({
    marks, rings, flash,
    hover: S.hover,
    showPad: false,
  });
}

function drawPad() {
  if (!pad) return;
  const cv = $('pad-canvas');
  if (cv.clientWidth !== pad.cssW || cv.clientHeight !== pad.cssH) {
    fitCanvas(cv, pad);
  }
  pad.draw({
    marks: new Map(),
    rings: [],
    flash: flashNow(),
    hover: S.padHover,
    showPad: true,
  });
}

function updateProgressUI() {
  const tl = S.timeline;
  const total = tl ? Math.max(0.001, tl.total_sec) : 1;
  const sl = $('progress');
  if (document.activeElement !== sl) {
    sl.value = String(Math.round((S.sec / total) * 1000));
  }
  $('time-now').textContent = fmtTime(S.sec);
  $('time-total').textContent = fmtTime(tl ? tl.total_sec : 0);
}

// ---------------------------------------------------------------------------
// 训练模式
// ---------------------------------------------------------------------------

/** 序列 = 把所有音**摊平**成一个音名列表（休止符跳过）。 */
function buildTrainSeq() {
  S.trainSeq = [];
  S.trainGaps = [];
  if (!S.timeline) return;
  const items = S.timeline.items;
  for (let i = 0; i < items.length; i++) {
    for (const p of items[i].chord.pitches) {
      S.trainSeq.push(p);
      const next = items[i + 1];
      S.trainGaps.push(next ? Math.max(0.15, next.start_sec - items[i].start_sec) : 0.5);
    }
  }
}

function startTrain() {
  if (!S.trainSeq.length) {
    toast('这份谱面没有可练的音');
    return;
  }
  S.train = true;
  S.trainI = 0;
  S.trainT0 = performance.now() / 1000;
  pause();
  S.sec = 0;
  syncModes();
  $('play-hint').textContent = '训练：按顺序点，点对了才会走下一个';
  toast('训练开始 —— 点音符顺序走');
}

function stopTrain() {
  if (!S.train) return;
  S.train = false;
  syncModes();
  $('play-hint').textContent = '';
}

/** 训练模式下的画面：只画"下一个该按的"。 */
function trainFrame() {
  const marks = new Map();
  const rings = [];
  const i = S.trainI;
  if (i >= S.trainSeq.length) return { marks, rings };

  const cell = L.pitch_to_cell(S.trainSeq[i]);
  if (!cell) return { marks, rings };

  marks.set(cell[0] + ',' + cell[1], { rank: 0, count: 1 });

  // 往后看几个 —— 「后面还有几个的显示」（用户 16.72 那条要求）
  for (let n = 1; n <= Math.min(3, S.trainSeq.length - i - 1); n++) {
    const c2 = L.pitch_to_cell(S.trainSeq[i + n]);
    if (!c2) continue;
    const key = c2[0] + ',' + c2[1];
    if (!marks.has(key)) marks.set(key, { rank: n, count: 1 });
  }

  // ★ 间隔提示：圈按这个音到下一个音的间隔缩 ★
  //   「要显示下一个按键的倒计时但是不用倒计时只需要显示就行」——
  //   所以圈一直挂在那儿，缩到中心就是"该点下一个了"。
  const gap = S.trainGaps[i] || 0.5;
  const elapsed = performance.now() / 1000 - S.trainT0;
  const frac = Math.max(0, Math.min(1, 1 - elapsed / gap));
  rings.push({ row: cell[0], col: cell[1], frac });

  return { marks, rings };
}

function trainHit(pitch) {
  if (!S.train) return;
  const want = S.trainSeq[S.trainI];
  if (pitch === want) {
    S.trainI += 1;
    S.trainT0 = performance.now() / 1000;
    if (S.trainI >= S.trainSeq.length) {
      toast('练完了 —— 一共 ' + S.trainSeq.length + ' 个音');
      stopTrain();
    }
  } else {
    // 点错了：出声但不前进（跟桌面版同一条规矩）
    flashCell(...(L.pitch_to_cell(pitch) || [0, 0]));
  }
}

// ---------------------------------------------------------------------------
// 点格子
// ---------------------------------------------------------------------------

function onPadPress(row, col, { fromEditor }) {
  S.player.unlock();
  const pitch = L.cell_to_pitch(row, col);
  S.player.play(pitch);
  if (fromEditor) flashCell(row, col);
  else flashCell(row, col);

  if (S.train && !fromEditor) {
    trainHit(pitch);
    return;
  }
  if (S.karaoke && !fromEditor) {
    // 跟打：点一下 = 记录（这里不做"跟着谱面走"的判定，出声就够）
    return;
  }
  if (fromEditor) {
    writeNoteToSheet(pitch);
  }
}

function writeNoteToSheet(pitch) {
  if (!$('chk-write').checked) return;
  const ta = $('sheet-text');
  const cur = ta.value;
  ta.value = cur + (cur && !/\s$/.test(cur) ? ' ' : '') + pitch;
  onTextChanged();
}

// ---------------------------------------------------------------------------
// 页面切换
// ---------------------------------------------------------------------------

function showPage(name) {
  for (const el of document.querySelectorAll('.page')) {
    el.classList.toggle('is-active', el.id === 'page-' + name);
  }
  for (const el of document.querySelectorAll('.tab')) {
    el.classList.toggle('is-active', el.dataset.page === name);
  }
  // 切回练琴页时重新量一次画布 —— 之前它是 display:none，
  // clientWidth 是 0，量出来的几何量全错。
  requestAnimationFrame(() => {
    if (name === 'play' && stage) fitCanvas($('stage'), stage);
    if (name === 'edit' && pad) fitCanvas($('pad-canvas'), pad);
  });
}

// ---------------------------------------------------------------------------
// 曲库
// ---------------------------------------------------------------------------

function refreshList() {
  const ul = $('sheet-list');
  ul.innerHTML = '';
  const names = S.store.list();
  if (!names.length) {
    const li = document.createElement('li');
    li.className = 'empty-tip';
    li.innerHTML = '还没有谱面。<br>点「新建」写一份，或者「导入 .txt」<br>把电脑上的谱面拿过来。';
    ul.appendChild(li);
    return;
  }
  for (const name of names) {
    const li = document.createElement('li');
    if (name === S.sheetName) li.classList.add('is-cur');

    const span = document.createElement('span');
    span.className = 'sl-name';
    span.textContent = name;

    const meta = document.createElement('span');
    meta.className = 'sl-meta';
    try {
      const tl = new Timeline(parse(S.store.get(name)));
      meta.textContent = tl.items.length + ' 音';
    } catch (e) {
      meta.textContent = '';
    }

    const del = document.createElement('button');
    del.className = 'sl-del';
    del.textContent = '✕';
    del.title = '删掉';
    del.onclick = (ev) => {
      ev.stopPropagation();
      if (confirm('删掉「' + name + '」？删了就找不回来了。')) {
        S.store.del(name);
        if (S.sheetName === name) {
          S.sheetName = '';
          S.sheetText = '';
          S.timeline = null;
          refreshHeader();
        }
        refreshList();
        toast('已删掉 ' + name);
      }
    };

    li.appendChild(span);
    li.appendChild(meta);
    li.appendChild(del);
    li.onclick = () => {
      loadSheet(name);
      showPage('play');
    };
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------------------
// 制谱
// ---------------------------------------------------------------------------

function onTextChanged() {
  const text = $('sheet-text').value;
  S.sheetText = text;
  try {
    S.timeline = new Timeline(parse(text));
    refreshHeader();
  } catch (e) {
    // 打字打到一半解析不了很正常，别弹错误框
  }
}

/**
 * 把时间轴上的改动写回文本。
 *
 * ★ 为什么写的是「秒:音高」这种格式 ★
 *   这正是 `parser` 认的"时间轴谱面"写法（`sheet.free`）——
 *   每个音自己带绝对时间，可以左右挪、可以重叠。
 *   老写法（`5 3 5`）的位置是"前一个音 + 时值"推出来的，
 *   向左挪等于要求负间距，数学上就表达不了。
 *   所以在时间轴上拖动之后，必须落到这个格式上，
 *   而且这份文本拿回**电脑版**照样能读 —— 两边是同一种文件。
 */
function syncTimelineToText() {
  if (!S.timeline) return;
  const tl = S.timeline;
  const title = tl.sheet && tl.sheet.title ? tl.sheet.title : (S.sheetName || '未命名');
  const bpm = tl.bpm_points && tl.bpm_points.length ? tl.bpm_points[0][1] : 120;

  const parts = [];
  for (const it of tl.items) {
    if (it.chord.is_rest) continue;
    // 0.5 -> "0.5"、1.25 -> "1.25"、2.0 -> "2"
    let s = it.start_sec.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
    parts.push(s + ':' + it.chord.pitches.join('&'));
  }

  const text = title + '\n#' + bpm + '#\n' + parts.join(' ');
  $('sheet-text').value = text;
  S.sheetText = text;
  // 重新解析一遍，让 items 的时值/顺序跟文本对齐
  try {
    S.timeline = new Timeline(parse(text));
    refreshHeader();
  } catch (e) { /* 不该发生 */ }
}

function openEditor(name) {
  editingName = name;
  S.sheetName = name;
  S.sheetText = S.store.get(name) || '';
  $('sheet-text').value = S.sheetText;
  onTextChanged();
  showPage('edit');
  toast('正在编辑：' + name);
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

function syncModes() {
  for (const b of document.querySelectorAll('.mode')) {
    const m = b.dataset.mode;
    b.classList.toggle('is-on',
      (m === 'karaoke' && S.karaoke) || (m === 'tap' && S.tap) || (m === 'train' && S.train));
  }
}

function bindEvents() {
  // ---- 底部标签栏 ----
  for (const t of document.querySelectorAll('.tab')) {
    t.onclick = () => showPage(t.dataset.page);
  }

  // ---- 顶栏 ----
  $('btn-sheets').onclick = () => showPage('lib');
  $('btn-more').onclick = () => {
    const el = $('play-hint');
    el.textContent = el.textContent ? '' : '点曲库挑一份谱面；播放时格子会依次亮';
  };

  // ---- 播放控制 ----
  $('btn-play').onclick = () => {
    S.player.unlock();
    if (S.playing) pause();
    else play();
  };
  $('btn-home').onclick = () => { home(); toast('回到开头'); };
  $('btn-stop').onclick = () => { stop(); toast('已停止'); };

  $('progress').oninput = (e) => {
    if (!S.timeline) return;
    S.sec = (Number(e.target.value) / 1000) * S.timeline.total_sec;
    S._soundPtr = nextIndexAt(S.sec);
    $('time-now').textContent = fmtTime(S.sec);
  };

  // ---- 模式开关 ----
  for (const b of document.querySelectorAll('.mode')) {
    b.onclick = () => {
      const m = b.dataset.mode;
      S.player.unlock();
      if (m === 'karaoke') {
        S.karaoke = !S.karaoke;
        if (S.karaoke) toast('跟打：开着 —— 播放不放原声，点格子才出声');
      } else if (m === 'tap') {
        S.tap = !S.tap;
        toast(S.tap ? '可按：格子能点着发声' : '可按：关着');
      } else if (m === 'train') {
        if (S.train) stopTrain();
        else startTrain();
        return;
      }
      syncModes();
    };
  }

  // ---- 练琴页的格子 ----
  const cv = $('stage');
  const toLocal = (ev) => {
    const r = cv.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  };
  cv.addEventListener('pointerdown', (ev) => {
    if (!stage) return;
    ev.preventDefault();
    const [x, y] = toLocal(ev);
    const cell = stage.cellAt(x, y);
    if (!cell) return;
    if (S.tap || S.karaoke || S.train) onPadPress(cell[0], cell[1], { fromEditor: false });
  });
  cv.addEventListener('pointermove', (ev) => {
    if (!stage) return;
    const [x, y] = toLocal(ev);
    S.hover = stage.cellAt(x, y);
  });
  cv.addEventListener('pointerleave', () => { S.hover = null; });

  // ---- 制谱页 ----
  for (const t of document.querySelectorAll('.etab')) {
    t.onclick = () => {
      const k = t.dataset.etab;
      for (const x of document.querySelectorAll('.etab')) {
        x.classList.toggle('is-active', x === t);
      }
      for (const x of document.querySelectorAll('.epanel')) {
        x.classList.toggle('is-active', x.id === 'ep-' + k);
      }
      if (k === 'pads') requestAnimationFrame(() => pad && fitCanvas($('pad-canvas'), pad));
      if (k === 'timeline') {
        requestAnimationFrame(() => {
          if (!tled) return;
          fitCanvas($('tl-canvas'), tled);
          // 一进来就把视野对准第一个音，别让用户对着空白找
          tled.scrollX = 0;
        });
      }
    };
  }

  $('sheet-text').oninput = onTextChanged;

  // ---- 时间轴：单指横拖滚、点音符选中、按住拖动改时间 ----
  const tcv = $('tl-canvas');
  let drag = null;

  const localXY = (ev, el) => {
    const r = el.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  };

  tcv.addEventListener('pointerdown', (ev) => {
    if (!tled) return;
    ev.preventDefault();
    tcv.setPointerCapture(ev.pointerId);
    const [x, y] = localXY(ev, tcv);
    const items = S.timeline ? S.timeline.items : [];
    const hit = tled.itemAt(items, x, y);
    if (hit !== null) {
      tled.sel = hit;
      const g = tled.geom(items, hit);
      drag = { mode: 'note', idx: hit, dx: x - g.x };
    } else {
      tled.sel = null;
      drag = { mode: 'scroll', x0: x, scroll0: tled.scrollX };
    }
  });

  tcv.addEventListener('pointermove', (ev) => {
    if (!drag || !tled) return;
    const [x] = localXY(ev, tcv);
    if (drag.mode === 'scroll') {
      tled.scrollX = Math.max(0, drag.scroll0 - (x - drag.x0));
      return;
    }
    const items = S.timeline ? S.timeline.items : [];
    const it = items[drag.idx];
    if (!it) return;
    // 改时间：跟着手指走，吸附到 0.05 秒（免得拖出 1.234567 这种数）
    const raw = Math.max(0, tled.xToSec(x - drag.dx));
    it.start_sec = Math.round(raw / 0.05) * 0.05;
    it.end_sec = it.start_sec;      // 时值交给"铺到下一个音"，这里不用管
    it.chord.at = it.start_sec;
  });

  const endDrag = () => {
    if (drag && drag.mode === 'note') {
      // 拖完把改动写回文本 —— 不然"保存"存下去的还是旧的
      syncTimelineToText();
    }
    drag = null;
  };
  tcv.addEventListener('pointerup', endDrag);
  tcv.addEventListener('pointercancel', endDrag);

  $('btn-apply').onclick = () => { onTextChanged(); toast('已应用'); };
  $('btn-clear-all').onclick = () => {
    if (!confirm('清空谱面文本？')) return;
    $('sheet-text').value = '';
    onTextChanged();
  };
  $('btn-sample').onclick = () => {
    const ta = $('sheet-text');
    ta.value = (ta.value ? ta.value + '\n' : '') + '小星星\n#120#\n1 1 5 5 6 6 5-\n4 4 3 3 2 2 1-';
    onTextChanged();
  };

  const pcv = $('pad-canvas');
  pcv.addEventListener('pointerdown', (ev) => {
    if (!pad) return;
    ev.preventDefault();
    const r = pcv.getBoundingClientRect();
    const cell = pad.cellAt(ev.clientX - r.left, ev.clientY - r.top);
    if (cell) onPadPress(cell[0], cell[1], { fromEditor: true });
  });

  // ---- 时间轴的缩放 / 删除 ----
  $('btn-tl-zoom-in').onclick = () => {
    if (!tled) return;
    tled.pxPerSec = Math.min(400, tled.pxPerSec * 1.4);
  };
  $('btn-tl-zoom-out').onclick = () => {
    if (!tled) return;
    tled.pxPerSec = Math.max(16, tled.pxPerSec / 1.4);
  };
  $('btn-tl-undo').onclick = () => {
    toast('手机上先不做撤销 —— 拖错了再拖回去就行', 2600);
  };
  $('btn-tl-del').onclick = () => {
    if (!S.timeline || !tled || tled.sel === null) {
      toast('先在时间轴上点一个音符');
      return;
    }
    const tl = S.timeline;
    const it = tl.items[tled.sel];
    if (!it) return;
    // ★ 删的是 `sheet.events` 里那个事件，然后整个重建 Timeline ★
    //   只删 `items` 是没用的 —— `items` 是从 `events` 派生出来的，
    //   下一次重建就回来了。
    const k = tl.sheet.events.indexOf(it.chord);
    if (k < 0) { toast('没找到这个音'); return; }
    tl.sheet.events.splice(k, 1);
    S.timeline = new Timeline(tl.sheet);
    tled.sel = null;
    syncTimelineToText();
    toast('删掉了 ' + it.chord.pitches.join('&'));
  };

  $('btn-save').onclick = () => {
    if (!editingName) { toast('先另存为'); return; }
    S.store.put(editingName, $('sheet-text').value);
    refreshList();
    toast('已保存 ' + editingName);
  };
  $('btn-save-as').onclick = () => {
    const name = prompt('存成什么名字？', editingName || '新谱面');
    if (!name) return;
    const clean = name.trim();
    if (!clean) return;
    editingName = S.store.freeName(clean);
    S.store.put(editingName, $('sheet-text').value);
    refreshList();
    toast('已存成 ' + editingName);
  };
  $('btn-editor-close').onclick = () => showPage('play');

  // ---- 曲库页 ----
  $('btn-new-sheet').onclick = () => {
    const name = S.store.freeName('新谱面');
    S.store.put(name, name + '\n#120#\n');
    refreshList();
    openEditor(name);
  };
  $('btn-import').onclick = async () => {
    const picked = await pickTextFile();
    if (!picked) return;
    const name = S.store.freeName(stripExt(picked.name) || '导入的谱面');
    S.store.put(name, picked.text);
    refreshList();
    loadSheet(name);
    showPage('play');
    toast('导入成功：' + name);
  };
  $('btn-export').onclick = async () => {
    if (!S.sheetName) { toast('先挑一份谱面'); return; }
    const text = S.store.get(S.sheetName) || '';
    const saved = downloadText(S.sheetName + '.txt', text);
    // ★ 两条路一起给 ★
    //   装成 APK 之后是套在安卓 WebView 里跑的，`<a download>`
    //   不一定被接住（各家实现不一致），而我没法在真机上验证。
    //   所以下载照常触发，同时把文本塞进剪贴板 —— 万一没弹保存框，
    //   用户粘一下就能拿到。至少保证有一条路是通的。
    const copied = await copyText(text);
    if (saved && copied) {
      toast('已导出 ' + S.sheetName + '.txt（文本也复制到剪贴板了）', 3000);
    } else if (copied) {
      toast('谱面已复制到剪贴板 —— 粘贴到电脑上存成 .txt 就行', 3600);
    } else if (saved) {
      toast('已导出 ' + S.sheetName + '.txt', 2600);
    } else {
      toast('导出失败 —— 可以试试「另存为」或者复制文本', 3200);
    }
  };
  $('btn-refresh').onclick = () => { refreshList(); toast('已刷新'); };

  // 画布尺寸跟着窗口变
  window.addEventListener('resize', () => {
    if (stage) fitCanvas($('stage'), stage);
    if (pad) fitCanvas($('pad-canvas'), pad);
  });
  window.addEventListener('orientationchange', () => {
    setTimeout(() => {
      if (stage) fitCanvas($('stage'), stage);
      if (pad) fitCanvas($('pad-canvas'), pad);
    }, 250);
  });
}

async function boot() {
  stage = new StageRenderer($('stage'));
  pad = new StageRenderer($('pad-canvas'));
  tled = new TimelineEditor($('tl-canvas'));

  bindEvents();

  // ★ 第一次打开时塞一份示例 ★
  //   对着一个空屏幕用户不知道能干什么，而这份"小星星"跟电脑版
  //   `sheets/demo.txt` 是同一份 —— 两边看到的第一个谱面是一样的。
  if (S.store.list().length === 0) {
    S.store.put('小星星', DEMO_SHEET);
    S.store.put('示例 · 时间轴', DEMO_FREE);
  }

  refreshList();

  // 有上次打开的谱面就接着用
  const cur = S.store.current;
  const names = S.store.list();
  if (cur && S.store.has(cur)) loadSheet(cur, { silent: true });
  else if (names.length) loadSheet(names[0], { silent: true });
  refreshHeader();

  // `?page=edit` / `?page=lib` 可以直接打开某一页（截图、分享链接用）
  // `&etab=timeline` 还能直接切到制谱页的某个子标签
  const qs = new URLSearchParams(location.search);
  showPage(qs.get('page') || 'play');
  const wantEtab = qs.get('etab');
  if (wantEtab) {
    const btn = document.querySelector('.etab[data-etab="' + wantEtab + '"]');
    if (btn) requestAnimationFrame(() => btn.click());
  }
  // 第一帧先把画布量准 —— 页面刚切过来时布局还没稳定，
  // 这一下不做的话会拿 0 或旧尺寸去画。
  requestAnimationFrame(() => {
    fitCanvas($('stage'), stage);
    fitCanvas($('pad-canvas'), pad);
  });
  requestAnimationFrame((t) => {
    S.lastTick = t;
    tick(t);
  });

  // 音源在后台慢慢载，不挡着界面出来
  const n = await S.player.load();
  if (n > 0) toast('音源就绪（' + n + ' 个音）');
  else toast('音源没载入 —— 点格子不会出声');
}

boot();
