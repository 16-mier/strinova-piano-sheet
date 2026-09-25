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
  try {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * 把文本放到剪贴板。
 *
 * ★ 为什么导出要配一个"复制" ★
 *   导出走的是 `<a download>` —— 浏览器里没问题，但**装成 APK 之后
 *   是套在安卓的 WebView 里**，那个下载行为不一定被接住
 *   （各家 WebView 对 `download` 属性的支持不一致）。
 *
 *   而我没法在真机上验证这一点（手上没有安卓设备）。
 *   与其赌它一定行，不如**两条路一起给**：下载照常触发，
 *   文本同时进剪贴板 —— 下载没弹框的话，用户粘一下就能拿到，
 *   至少这条路是通的。
 */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    // 有些环境（老的 WebView / 非安全上下文）不让写剪贴板
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e2) {
      return false;
    }
  }
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
