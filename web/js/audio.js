// 播放琴音 —— 用 Web Audio 直接放采样，延迟低、能同时响好几个音。
//
// ★ 跟桌面版的对应关系 ★
//   桌面版 `ui/keypad.py::NotePlayer` 用的是 QSoundEffect，而且**每个音
//   备了 3 个实例轮换** —— 不然同一个键快速连按会互相打断（后一声把
//   前一声掐掉）。Web Audio 这边天生就支持"同一个 buffer 同时播多份"
//   （每次 `start()` 都是一个独立的 source node），所以不需要那个池子。
//
// ★ 采样文件名 ★
//   `core/notes.py::sample_path()` 定的规矩：Windows 文件名不能有撇号，
//   所以 `1'` 存成 `1_up.wav`、`1''` 存成 `1_up_up.wav`。
//   手机版沿用同一批文件 —— 音色跟桌面版是一模一样的。

import { all_pitches } from './layout.js';

export class NotePlayer {
  constructor() {
    this.ctx = null;
    this.buffers = new Map();
    this.volume = 0.75;
    this.ready = false;
    this.loaded = 0;
  }

  /** 音名 -> 采样文件名（跟 `core/notes.py::sample_path` 同一条规矩）。 */
  static fileName(pitch) {
    return pitch.replace(/'/g, '_up') + '.wav';
  }

  /**
   * 载入 16 个采样。
   *
   * ★ AudioContext 必须等一次用户手势才能出声 ★
   *   浏览器的自动播放策略：没交互就创建的 AudioContext 会停在
   *   `suspended`，`start()` 调了也是静音。所以这里只负责**建好并解码**，
   *   真正 `resume()` 要等用户第一次点屏幕（见 `unlock()`）。
   */
  async load(onProgress) {
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC();

    const pitches = all_pitches();
    let done = 0;
    await Promise.all(pitches.map(async (p) => {
      const url = 'assets/notes/' + NotePlayer.fileName(p);
      try {
        const res = await fetch(url);
        const arr = await res.arrayBuffer();
        const buf = await this.ctx.decodeAudioData(arr);
        this.buffers.set(p, buf);
        done += 1;
        if (onProgress) onProgress(done, pitches.length);
      } catch (e) {
        // 少一个音不该把整个程序拖垮 —— 那个键点下去不出声而已
        console.warn('音源载入失败：' + url, e);
      }
    }));

    this.loaded = this.buffers.size;
    this.ready = this.loaded > 0;
    return this.loaded;
  }

  /** 用户第一次交互时调 —— 把 AudioContext 从 suspended 里唤醒。 */
  unlock() {
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
  }

  /** 放一个音。音量不传就用默认的。 */
  play(pitch, vol) {
    if (!this.ctx || !this.ready) return false;
    const buf = this.buffers.get(pitch);
    if (!buf) return false;
    try {
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      const gain = this.ctx.createGain();
      gain.gain.value = vol === undefined ? this.volume : vol;
      src.connect(gain);
      gain.connect(this.ctx.destination);
      src.start();
      return true;
    } catch (e) {
      return false;
    }
  }

  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, v));
  }
}
