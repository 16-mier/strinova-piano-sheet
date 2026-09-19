// 谱面仓库 —— 存在手机本地，可以导入 / 导出。
//
// ★ 谱面就是普通的 .txt 文件 ★
//   手机版和桌面版用的是**同一套格式**（`core/parser.py` 认得的那种）：
//   第一行曲名、第二行 `#BPM#`、后面是音名。所以互通不需要任何转换 ——
//   桌面版 `sheets/` 里的文件直接导入就能用，这边导出的文件丢回
//   桌面版的 `sheets/` 也照样能读。

const KEY = 'kaqiu.sheets.v1';
const CURRENT = 'kaqiu.current.v1';

export class SheetStore {
  constructor() {
    this.sheets = this._load();
  }

  _load() {
    try {
      const raw = localStorage.getItem(KEY);
      const obj = raw ? JSON.parse(raw) : null;
      return obj && typeof obj === 'object' ? obj : {};
    } catch (e) {
      return {};
    }
  }

  _persist() {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.sheets));
      return true;
    } catch (e) {
      // 存储满了 / 隐私模式下禁用 —— 别让它把程序带走
      console.warn('谱面存不进去', e);
      return false;
    }
  }

  /** 按名字排序的谱面名列表。 */
  list() {
    return Object.keys(this.sheets).sort((a, b) => a.localeCompare(b, 'zh'));
  }

  has(name) {
    return Object.prototype.hasOwnProperty.call(this.sheets, name);
  }

  get(name) {
    return this.sheets[name];
  }

  put(name, text) {
    this.sheets[name] = text;
    return this._persist();
  }

  del(name) {
    delete this.sheets[name];
    this._persist();
  }

  /** 给一个不重名的名字（`新谱面` → `新谱面 2` → …）。 */
  freeName(base) {
    if (!this.has(base)) return base;
    for (let i = 2; i < 1000; i++) {
      const n = base + ' ' + i;
      if (!this.has(n)) return n;
    }
    return base + ' ' + Date.now();
  }

  get current() {
    try {
      return localStorage.getItem(CURRENT) || '';
    } catch (e) {
      return '';
    }
  }

  set current(name) {
    try {
      localStorage.setItem(CURRENT, name || '');
    } catch (e) { /* 忽略 */ }
  }
}

/** 把一段文本存成文件下载下来（导出）。 */
export function downloadText(filename, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** 让用户挑一个 .txt 读进来（导入）。 */
export function pickTextFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.txt,text/plain';
    input.onchange = () => {
      const file = input.files && input.files[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      reader.onload = () => resolve({ name: file.name, text: String(reader.result) });
      reader.onerror = () => resolve(null);
      reader.readAsText(file, 'utf-8');
    };
    input.click();
  });
}

/** 文件名去掉扩展名（导入时拿它当谱面名）。 */
export function stripExt(filename) {
  return String(filename || '').replace(/\.txt$/i, '');
}
