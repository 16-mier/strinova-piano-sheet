// 编译后的谱子，供界面按时间查询。
//
// （从 `core/timeline.py` 移植，两边行为必须一致。字段名保持 Python 的蛇形写法，
//   这样跟 parser / edit_model 的移植版本对得上。）

import { Sheet, Chord, BpmChange } from './parser.js';

export const DEFAULT_BPM = 120;

/** 一个已定位到时间轴上的和弦。 */
export class TimedChord {
  constructor(chord, index, start_beat, start_sec, end_sec, bpm) {
    this.chord = chord;
    this.index = index;        // 在谱子里是第几个和弦（不含 BPM 事件）
    this.start_beat = start_beat;
    this.start_sec = start_sec;
    this.end_sec = end_sec;
    this.bpm = bpm;            // 弹这个音时的 BPM
  }

  get duration_sec() {
    return this.end_sec - this.start_sec;
  }
}

export class Timeline {
  constructor(sheet, default_bpm = DEFAULT_BPM) {
    this.sheet = sheet;
    this.items = [];
    this.bpm_points = [];      // [秒, BPM]

    let bpm = default_bpm;
    let t = 0.0;
    let beat = 0.0;
    let idx = 0;

    // bpm_points 完全由事件流填充；曲子开头的 #BPM# 覆盖默认值
    for (const ev of sheet.events) {
      if (ev instanceof BpmChange) {
        if (ev.bpm > 0) {
          bpm = ev.bpm;
          // 同一个时间点重复出现直接覆盖，避免 (0,120)(0,100) 这种脏数据
          const last = this.bpm_points[this.bpm_points.length - 1];
          if (last && last[0] === t) {
            this.bpm_points[this.bpm_points.length - 1] = [t, bpm];
          } else {
            this.bpm_points.push([t, bpm]);
          }
        }
        continue;
      }

      // ★ 位置直接读 `at`（绝对秒）★
      //   `parser._finalize_free` 已经把两种写法都摊平成这个模型了。
      //   时值还是"拍"（下游一直用这个单位），按当时的 BPM 折算成秒。
      const at = ev.at === undefined ? null : ev.at;
      const start_sec = at === null ? t : Number(at);
      const dur_sec = Math.max(0.0, ev.duration) * 60.0 / bpm;
      this.items.push(new TimedChord(
        ev, idx,
        start_sec / Math.max(1e-9, 60.0 / bpm),
        start_sec, start_sec + dur_sec,
        bpm,
      ));
      t = start_sec + dur_sec;
      beat += ev.duration;
      idx += 1;
    }

    if (this.bpm_points.length === 0) {
      this.bpm_points.push([0.0, default_bpm]);
    } else if (this.bpm_points[0][0] > 0.0) {
      this.bpm_points.unshift([0.0, default_bpm]);
    }

    this.total_sec = t;
    this.total_beats = beat;
  }

  get length() {
    return this.items.length;
  }

  /** Python 的 `__bool__`：有空才有内容。 */
  get isEmpty() {
    return this.items.length === 0;
  }

  // ---------- 按时间查询 ----------

  /**
   * 当前正在第几个音符（已过完的算前一个）。空谱返回 -1。
   *
   * 二分查找：找**最后一个** `start_sec <= sec` 的下标。
   * 注意 `mid = (lo + hi + 1) / 2` 里的 `+1` —— 没有它会在
   * "相邻两个都满足"时死循环（`lo` 永远推不上去）。
   */
  index_at(sec) {
    if (this.items.length === 0) return -1;
    let lo = 0;
    let hi = this.items.length - 1;
    if (sec < this.items[0].start_sec) return -1;
    while (lo < hi) {
      const mid = Math.floor((lo + hi + 1) / 2);
      if (this.items[mid].start_sec <= sec) {
        lo = mid;
      } else {
        hi = mid - 1;
      }
    }
    return lo;
  }

  item_at(sec) {
    const i = this.index_at(sec);
    return i >= 0 ? this.items[i] : null;
  }

  /** 从当前时刻起，往后数 count 个待弹的音（含当前正在响的那个）。 */
  upcoming(sec, count = 6) {
    if (this.items.length === 0) return [];
    let start = this.index_at(sec);
    if (start < 0) start = 0;
    return this.items.slice(start, start + count);
  }

  bpm_at(sec) {
    let bpm = this.bpm_points.length ? this.bpm_points[0][1] : DEFAULT_BPM;
    for (const [at, value] of this.bpm_points) {
      if (at <= sec) bpm = value;
      else break;
    }
    return bpm;
  }

  seconds_per_beat(sec = 0.0) {
    return 60.0 / Math.max(1, this.bpm_at(sec));
  }

  // ---------- 统计 ----------

  stats() {
    if (this.items.length === 0) return '空谱';
    // ★ 只报秒 ★ —— 用户：「完全按照时间轴来，去掉节拍这个东西」。
    //   BPM 还留着（谱面里本来就标了），但它只是记谱时的历史信息，
    //   不代表任何东西落在哪一秒上。
    const 变速 = this.bpm_points.length > 1 ? '（含变速）' : '';
    return `${this.items.length} 个音 · 共 ${this.total_sec.toFixed(1)} 秒 · BPM ${this.bpm_points[0][1]}${变速}`;
  }

  all_pitches() {
    const out = [];
    for (const it of this.items) out.push(...it.chord.pitches);
    return out;
  }
}

/**
 * 从「时间轴谱面」的音符列表**直接**搭一个 Timeline，不经过文本。
 *
 * ★ 契约：`notes[i].start` 必须是**【秒】**，不是【拍】★
 *
 *   这条以前没写清楚，于是真的出过事（2026-xx）：
 *   制谱器那边的 `EdNote.start` 单位是**拍**（`core/edit_model.py` 里
 *   `start = 秒 / SPB`），却被原样递了进来。结果浮窗拿到的时间轴
 *   整体**放大一倍**（demo.txt：主界面 28.8 秒 vs 制谱器 55.2 秒），
 *   而喂给它的播放位置是真实秒 —— 浮窗只走到"已播放时长的一半"，
 *   越弹越落后，用户看到的就是「都下俩个按键了显示还是上俩个」。
 *
 *   修法是在**调用方**换算（`ui/editor.py` 的 `_SecNote`）——
 *   因为这个函数**故意不 import `SPB`**，所以单位换算只能由交出数据的一方负责。
 *
 * ★ 时值一律记 0 ★
 *   浮窗只看**起点**（这个音什么时候该打），不看它响多久 ——
 *   制谱器里每个方块的长度本来就自动铺满到下一个音。
 */
export function timeline_from_notes(notes, bpm = DEFAULT_BPM) {
  const sheet = new Sheet();
  for (const n of notes) {
    if (n.is_rest) continue;
    const pitches = Array.from(n.pitches || []);
    if (pitches.length === 0) continue;
    sheet.events.push(new Chord({
      pitches,
      duration: 0.0,
      is_rest: false,
      raw: n.label || pitches.join('+'),
      at: Number(n.start || 0.0),
    }));
  }
  return new Timeline(sheet, bpm);
}
