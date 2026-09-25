// -*- coding: utf-8 -*-
/**
 * 谱面解析 —— 把记谱文本解析成事件流。
 *
 * ★ 这是 `core/parser.py` 的逐行移植 ★
 *   Python 原版继续保留并继续使用，两边**同一份谱面文本必须解析出一样的结果**
 *   （对拍脚本见项目外的 `_port_check_parser.py` / `_port_check_parser.mjs`）。
 *   所以这里有意做了不少"不像 JS"的事 —— 自己实现 Python 的 `%g`、银行家舍入、
 *   按**码点**算列号、照抄 `min`/`max` 遇到 NaN 的行为……
 *   每处都标了 `★ Python↔JS`，改之前先看一眼对应的 Python 行。
 *
 * 语法完全兼容 StrinovaPracticeRoom（猫弹琴 2.8.0），现成曲谱可直接用。
 *
 * 记谱速查
 * --------
 * 音高    中音 1~7 ｜ 半音 #4 ｜ 低音 6. ｜ 高音 3' ｜ 倍高音 1''
 * 节奏    基础 = 1 拍
 *         `-` 加法：每个 +1 拍
 *         `^` 乘法：每个 ×0.5（写在音符前或后都行）
 *         例：1-  = 2 拍      ^1   = 0.5 拍
 *             ^^1 = 0.25 拍   ^1-- = 1.5 拍（附点四分）
 *             ^^1-- = 0.75 拍（附点八分）
 * 休止    单独写 `-` 是休止 1 拍，`^-` 是休止 0.5 拍
 * 和弦    `&` 连接同时发声：1'&3'&5'，可带节奏 ^1&3&5
 * 变速    `#数字#` 在任意位置实时改 BPM，如 #156#
 * 排版    空格 / 换行只是排版，会被跳过
 *         `~` 是延音记号，不影响时长
 *         「不像乐谱的文字」会被整段跳过，且**不当作休止符**（不留空档）
 */

// ============================================================================
// 一、常量
// ============================================================================

/** 合法字符白名单 —— 与猫弹琴的 _is_valid_note 完全一致（Python 里是 `set`）。 */
export const ALLOWED_CHARS = new Set("0123456789^'-~\"&#.");

/** 计算时值前要剥离的修饰符（Python 里是 tuple，JS 里用数组等价）。 */
export const _STRIP_FOR_BODY = ['^', '-', '~', '&'];

/** 没写 `#BPM#` 时用的默认速度。 */
export const DEFAULT_BPM = 120;

// ★ 时间轴谱面的秒 ↔ 拍换算 ★
//   新写法（`秒:音高`）直接写秒，而 `Chord.duration` 这类老字段仍然按"拍"。
//   取 0.5 = BPM 120 —— **纯粹是个换算系数**，跟演奏速度无关。
//
//   ★ 但界面上**不再出现「拍」**★
//     用户：「完全按照时间轴来，去掉节拍这个东西」。
//     下面这个 0.5 只活在代码内部；凡是给人看的地方一律显示**秒**
//     —— 见 `ui/editor.py` 的 `lbl_pos` 和 `EditModel.stats()`。
//     所以读到这里别把它当成"用户概念里的拍"，它就是个 ×2 的刻度，
//     跟曲子标不标 `#120#` 也没有任何关系。
export const SPB = 0.5;

// ============================================================================
// 二、Python 内建行为的替身
//   ★ Python↔JS ★ 这一段在 Python 里全是**语言自带**的
//   （`round` / `%g` / `str.isdigit` / `str.strip` / `float()` / `int()` / `max`），
//   JS 里没有对应物、或者语义不一样，只能自己写一遍。
//   ★ 这些是"基础设施"，不是业务逻辑 ★ —— 业务逻辑请对照 `core/parser.py` 读。
// ============================================================================

/** Python 的 `ValueError`（只为让抛错类型能跟 Python 对上）。 */
export class ValueError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ValueError';
  }
}

/** Python 的 `OverflowError`。 */
export class OverflowError extends Error {
  constructor(message) {
    super(message);
    this.name = 'OverflowError';
  }
}

// ★ Python 的 `str.isspace()` 认哪些字符 ★
//   它**不是** JS 的 `\s`：Python 多认 \x1c~\x1f、也认 \x85，
//   而 JS 的 `\s` 认 \uFEFF（Python 不认）。
//   下面这份区间是从 CPython 逐码点扫出来的，别手改。
const _PY_WS_CLASS = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a'
  + '\\u2028\\u2029\\u202f\\u205f\\u3000';
const _PY_WS_RE = new RegExp('[' + _PY_WS_CLASS + ']');
const _PY_WS_TRIM_RE = new RegExp('^[' + _PY_WS_CLASS + ']+|[' + _PY_WS_CLASS + ']+$', 'g');

/**
 * Python 的 `str.isdigit()` 里"算数字"但**不在** `\p{Nd}` 里的那些码点
 * （上标 ²、圈码 ①、带括号 ⑴ ……）。也是从 CPython 扫出来的。
 */
const _DIGIT_EXTRA_RANGES = [
  [0xb2, 0xb3], [0xb9, 0xb9], [0x1369, 0x1371], [0x19da, 0x19da],
  [0x2070, 0x2070], [0x2074, 0x2079], [0x2080, 0x2089],
  [0x2460, 0x2468], [0x2474, 0x247c], [0x2488, 0x2490],
  [0x24ea, 0x24ea], [0x24f5, 0x24fd], [0x24ff, 0x24ff],
  [0x2776, 0x277e], [0x2780, 0x2788], [0x278a, 0x2792],
  [0x10a40, 0x10a43], [0x10e60, 0x10e68], [0x11052, 0x1105a], [0x1f100, 0x1f10a],
];
const _ND_RE = /\p{Nd}/u;
const _DIGIT_EXTRA_RE = new RegExp('[' + _DIGIT_EXTRA_RANGES
  .map(([a, b]) => (a === b
    ? `\\u{${a.toString(16)}}`
    : `\\u{${a.toString(16)}}-\\u{${b.toString(16)}}`))
  .join('') + ']', 'u');

/**
 * Python 的 `str.strip()`（无参数版）：去掉**两端**的 Python 空白。
 * ★ 跟 JS 的 `.trim()` 不一样 ★ `.trim()` 认 \uFEFF、不认 \x1c，两边正好错开。
 */
export function _stripPy(s) {
  return s.replace(_PY_WS_TRIM_RE, '');
}

/** 单个**码点**是不是 Python 眼里的空白（`'\x1c'.isspace()` 为真）。 */
export function _pyIsSpace(ch) {
  return _PY_WS_RE.test(ch);
}

/** 单个**码点**是不是 Python 眼里的数字（`c.isdigit()`）。 */
export function _isDigitChar(ch) {
  return _ND_RE.test(ch) || _DIGIT_EXTRA_RE.test(ch);
}

/**
 * Python 的 `str.isdigit()`（整串）：非空且**每个**字符都是数字。
 * ★ 注意跟 `any(c.isdigit())` 的区别 ★ 后者用 `_isDigitChar()`。
 */
export function _pyIsDigit(s) {
  if (s.length === 0) {
    return false;
  }
  for (const ch of s) {
    if (!_isDigitChar(ch)) {
      return false;
    }
  }
  return true;
}

/** Unicode 十进制数字（Nd）的数值。例：'٣' -> 3、'１' -> 1。 */
function _ndValue(ch) {
  const cp = ch.codePointAt(0);
  if (cp <= 0x39) {
    return cp - 0x30;                       // ASCII 快路径
  }
  // Nd 数字在每个连续段里都是 0~9 顺序排的（数学字母数字区是好几段连在一起），
  // 所以往上找到本段起点、再对 10 取模就是数值。
  let zero = cp;
  while (zero > 0 && _ND_RE.test(String.fromCodePoint(zero - 1))) {
    zero -= 1;
  }
  return (cp - zero) % 10;
}

/** 把串里的 Nd 数字换成 ASCII 数字（对齐 CPython 解析前的归一化）。 */
function _normalizeDigits(s) {
  let out = '';
  for (const ch of s) {
    out += _ND_RE.test(ch) ? String(_ndValue(ch)) : ch;
  }
  return out;
}

/**
 * Python 的 `float()`：解析不了返回 `null`（= 那边抛 ValueError，`parse` 接住）。
 *
 * ★ 不能直接用 JS 的 `Number()` ★ 两边差得远：
 *   `Number('')` 是 **0**（Python 抛异常）—— 不挡这一下，`0:` 这种写错的
 *   token 会被当成"第 0 秒"默默收下；`Number('0x10')` 是 16（Python 不认十六进制）；
 *   `Number('inf')` 是 NaN（Python 给 Infinity）；`Number('1_0')` 是 NaN（Python 给 10）。
 */
export function _pyFloat(text) {
  if (typeof text !== 'string') {
    return null;
  }
  let t = _normalizeDigits(_stripPy(text));
  if (t === '') {
    return null;
  }
  let sign = 1;
  const first = t[0];
  if (first === '+' || first === '-') {
    if (first === '-') {
      sign = -1;
    }
    t = t.slice(1);
  }
  const low = t.toLowerCase();
  if (low === 'inf' || low === 'infinity') {
    return sign * Infinity;
  }
  if (low === 'nan') {
    // ★ Python 的 nan 带符号 ★ `float('-nan')` 的位模式是 fff8...，`float('nan')`
    //   是 7ff8...；JS 的 `NaN` 只有正号那一份，所以负号得自己造一个。
    return sign < 0 ? _NEG_NAN : NaN;
  }
  // 十进制：整数部 / 小数部 / 指数部，各部分内部允许 PEP 515 的下划线
  if (!/^(?:[0-9][0-9_]*)?(?:\.(?:[0-9][0-9_]*)?)?(?:[eE][-+]?[0-9][0-9_]*)?$/.test(t)) {
    return null;
  }
  if (!/[0-9]/.test(t)) {
    return null;                            // '.', 'e5' 这种整体没有数字
  }
  if (!_underscoresOk(t)) {
    return null;                            // 下划线必须夹在两个数字中间
  }
  const v = Number(t.split('_').join(''));
  return Number.isNaN(v) ? null : sign * v;
}

/** 负号 NaN（位模式 fff8000000000000）—— JS 里造不出来，只能按位拼。 */
const _NEG_NAN = (() => {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setUint32(0, 0xfff80000);
  dv.setUint32(4, 0);
  return dv.getFloat64(0);
})();

/** PEP 515：下划线只能夹在数字中间（`1_0` 行，`_1` / `1_` / `1__0` 不行）。 */
function _underscoresOk(s) {
  for (let i = 0; i < s.length; i += 1) {
    if (s[i] === '_') {
      if (i === 0 || i === s.length - 1) {
        return false;
      }
      if (!/[0-9]/.test(s[i - 1]) || !/[0-9]/.test(s[i + 1])) {
        return false;
      }
    }
  }
  return true;
}

/**
 * Python 的 `int()`（十进制）：解析不了返回 `null`（= 那边抛 ValueError）。
 * ★ 只认 `\p{Nd}` 数字，不认 `isdigit()` 里那些圈码/上标 ★
 *   所以 `int('①')` 在 Python 里是抛 ValueError 的 —— `#①#` 那种谱面会让
 *   `parse()` 直接崩。这不是本移植版新加的毛病，见 `parse()` 里的说明。
 */
export function _pyInt(text) {
  if (typeof text !== 'string') {
    return null;
  }
  let t = _normalizeDigits(_stripPy(text));
  if (t === '') {
    return null;
  }
  let sign = 1;
  const first = t[0];
  if (first === '+' || first === '-') {
    if (first === '-') {
      sign = -1;
    }
    t = t.slice(1);
  }
  if (!/^[0-9][0-9_]*$/.test(t) || !_underscoresOk(t)) {
    return null;
  }
  // ★ 超过 2^53 的大整数这里会丢精度 ★ Python 有任意精度整数、JS 的 Number 没有。
  //   实际谱面里 BPM 最多三四位数，碰不到。
  return sign * Number(t.split('_').join(''));
}

/**
 * Python 的 `round(x)`（**无 ndigits**，结果是个整数）。
 * ★ 两个坑 ★
 *   1. Python 是**银行家舍入**（.5 进偶数）：`round(2.5) == 2`、`round(3.5) == 4`；
 *      JS 的 `Math.round` 是逢五进一（`Math.round(2.5) == 3`），负数还往 +∞ 偏。
 *   2. `round(nan)` 抛 ValueError、`round(inf)` 抛 OverflowError；
 *      JS 的 `Math.round` 会安安静静地给出 NaN / Infinity。
 */
export function _pyRound0(x) {
  if (Number.isNaN(x)) {
    throw new ValueError('cannot convert float NaN to integer');
  }
  if (!Number.isFinite(x)) {
    throw new OverflowError('cannot convert float infinity to integer');
  }
  const f = Math.floor(x);
  const diff = x - f;                       // 一定落在 [0, 1)
  if (diff > 0.5) {
    return f + 1;
  }
  if (diff < 0.5) {
    return f;
  }
  return f % 2 === 0 ? f : f + 1;           // 恰好 .5 —— 取偶数
}

/** 整数的 Python `str(int)` —— 要的是**精确**位数，不是 JS 那套最短表示。 */
export function _pyIntStr(n) {
  // ★ 别用 String(n) ★ 对整数值的 double，JS 给的是"最短能复原"的写法：
  //   `String(12345678901234567168)` 是 '12345678901234567000'，
  //   而 Python 的 `str(int(...))` 是 '12345678901234567168'（一位不差）。
  //   BigInt 转换拿到的就是那个 double 的精确整数值，位数自然对得上。
  return BigInt(n).toString();
}

/**
 * 一个正有限 double 的**精确**十进制展开：`x = digits × 10^exp10`（digits 已去尾零）。
 * 用途见 `_pyFmtG` —— 只有拿到精确值，才能像 Python 那样按**银行家舍入**取整。
 */
function _exactDecimalOfDouble(a) {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setFloat64(0, a);
  const hi = dv.getUint32(0);
  const lo = dv.getUint32(4);
  const expBits = (hi >>> 20) & 0x7ff;
  const frac = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let m;
  let e;
  if (expBits === 0) {                    // 次正规数
    m = frac;
    e = -1074;
  } else {                                // 正规数：还有个隐含的 1
    m = frac | (1n << 52n);
    e = expBits - 1075;
  }
  // x = m × 2^e：e ≥ 0 时是整数；e < 0 时乘上 5^-e 就是精确的十进制数字
  let digits;
  let exp10;
  if (e >= 0) {
    digits = m << BigInt(e);
    exp10 = 0;
  } else {
    digits = m * (5n ** BigInt(-e));
    exp10 = e;
  }
  while (digits % 10n === 0n) {
    digits /= 10n;
    exp10 += 1;
  }
  return { digits, exp10 };
}

/**
 * Python 的 `'%g' % x`（精度 6、去尾零、指数阈值 -4 / 6）。
 * ★ JS 没有 %g ★ `String(0.1 + 0.2)` 是 '0.30000000000000004'，
 * Python 的 `'%g'` 给 '0.3'；`'%g' % 0.00001` 是 '1e-05' 而不是 '0.00001'。
 *
 * ★ 而且不能拿 `toExponential` / `toPrecision` 凑 ★
 *   那两个在**正好半个**时一律进位（ECMA 规定"取大的那个"），
 *   Python 走 David Gay 的 dtoa —— 是**银行家舍入**：
 *     `'%g' % 1234565.0` → Python '1.23456e+06'，JS 的 toExponential(5) 会给 '1.23457e+6'。
 *   所以这里先把 double 的**精确**十进制位数用 BigInt 摊开，再自己按半进偶数砍。
 */
export function _pyFmtG(x, precision = 6) {
  if (Number.isNaN(x)) {
    return 'nan';
  }
  if (x === Infinity) {
    return 'inf';
  }
  if (x === -Infinity) {
    return '-inf';
  }
  if (x === 0) {
    return Object.is(x, -0) ? '-0' : '0';
  }
  const p = precision;
  const neg = x < 0;
  const { digits, exp10 } = _exactDecimalOfDouble(Math.abs(x));
  let ds = digits.toString();
  let X = exp10 + ds.length - 1;          // 最高位那一位的十进制指数

  if (ds.length > p) {                    // 砍到 p 位有效数字（半进偶数）
    const k = ds.length - p;
    const divisor = 10n ** BigInt(k);
    let q = digits / divisor;
    const r = digits % divisor;
    const twice = r * 2n;
    if (twice > divisor || (twice === divisor && q % 2n !== 0n)) {
      q += 1n;
    }
    ds = q.toString();
    if (ds.length > p) {                  // 进位顶上去一格（999999.9 → 1e6）
      X += 1;
      ds = ds.slice(0, p);
    }
  }
  ds = ds.replace(/0+$/, '');             // %g 去尾零
  if (ds === '') {
    ds = '0';
  }

  let out;
  if (X < -4 || X >= p) {                 // 指数写法
    const mant = ds.length > 1 ? ds[0] + '.' + ds.slice(1) : ds[0];
    out = mant + 'e' + (X < 0 ? '-' : '+') + String(Math.abs(X)).padStart(2, '0');
  } else if (X >= ds.length - 1) {        // 定点写法，整数部分还要补零
    out = ds + '0'.repeat(X - (ds.length - 1));
  } else if (X >= 0) {
    out = ds.slice(0, X + 1) + '.' + ds.slice(X + 1);
  } else {
    out = '0.' + '0'.repeat(-X - 1) + ds;
  }
  return neg ? '-' + out : out;
}

/**
 * Python 的 `max(a, b)`。
 * ★ 别直接换成 `Math.max` ★ 遇到 NaN 时两者相反：
 *   Python 的 `max(0.0, nan)` 返回 **0.0**（它只在"另一个更大"时才换），
 *   JS 的 `Math.max(0.0, NaN)` 返回 **NaN**。
 *   摊平那套算时值的代码真会吃到 NaN（谱面里写了 `nan:5` 就会）。
 */
export function _pyMax2(a, b) {
  return b > a ? b : a;
}

/** Python 的 `str.count()`（不重叠计数）。 */
export function _count(s, ch) {
  return s.split(ch).length - 1;
}

/**
 * 依次 `s.replace(ch, '')`（Python 的 replace 换掉**全部**，一次一个 ch）。
 * ★ JS 坑 ★ `'^^^'.replace('^', '')` 只换掉第一个 —— 必须 split/join。
 */
export function _stripChars(s, chars) {
  let out = s;
  for (const ch of chars) {
    out = out.split(ch).join('');
  }
  return out;
}

/**
 * Python 的 `str.splitlines()`（无参数）。
 * ★ 它认的换行边界比 JS 多 ★ \n \r \r\n \v \f \x1c \x1d \x1e \x85 \u2028 \u2029；
 *   而且**结尾的换行不产出空串**（'a\n'.splitlines() == ['a']）。
 */
export function _splitlines(text) {
  if (text === '') {
    return [];
  }
  const parts = text.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/);
  if (parts.length > 0 && parts[parts.length - 1] === ''
      && /[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]$/.test(text)) {
    parts.pop();
  }
  return parts;
}

/** Python 的 `str.partition(sep)`。 */
export function _pyPartition(s, sep) {
  const idx = s.indexOf(sep);
  if (idx < 0) {
    return [s, '', ''];
  }
  return [s.slice(0, idx), sep, s.slice(idx + sep.length)];
}

/** 码点长度（Python 的 `len()` 数码点，JS 的 `.length` 数 UTF-16 码元）。 */
export function _codePointLen(s) {
  return [...s].length;
}

/** Python 的 `'%-12s'`：左对齐补空格，超长**不截断**。 */
export function _padEnd12(s) {
  return s + ' '.repeat(Math.max(0, 12 - _codePointLen(s)));
}

// ============================================================================
// 三、数据类（对应 Python 的 @dataclass）
//   ★ 构造参数两种写法都收 ★
//     位置式 —— `new Chord(pitches, duration, is_rest, raw, line, col, at)`，
//               跟 Python 的 `@dataclass` 字段顺序一一对应；
//     对象式 —— `new Chord({pitches, duration, is_rest, raw, line, col, at})`，
//               web 这边原来的写法（`timeline.js` 在用），继续支持。
// ============================================================================

/** 一个和弦，或一个休止符。（对应 `@dataclass class Chord`） */
export class Chord {
  constructor(pitches, duration, is_rest, raw, line = 0, col = 0, at = null) {
    let opts = null;
    if (pitches !== null && typeof pitches === 'object'
        && !Array.isArray(pitches) && duration === undefined) {
      opts = pitches;                       // 对象式（web 原有写法）
    }
    if (opts) {
      this.pitches = opts.pitches;          // 和弦内各音，如 ['1', "3'"]；休止符为 []
      this.duration = opts.duration;        // 拍数
      this.is_rest = opts.is_rest;
      this.raw = opts.raw;                  // 原始 token（含修饰符）
      this.line = opts.line === undefined ? 0 : opts.line;
      this.col = opts.col === undefined ? 0 : opts.col;
      this.at = opts.at === undefined ? null : opts.at;
    } else {
      this.pitches = pitches;
      this.duration = duration;
      this.is_rest = is_rest;
      this.raw = raw;
      this.line = line;
      this.col = col;
      this.at = at;
    }
    // ★ 「时间轴谱面」专用：这个音的**绝对开始时间（秒）** ★
    //   `null` = 老格式（顺序记谱，位置靠前面的累计时值推出来）。
    //   不是 null 时，位置就是它自己说了算 —— 于是可以向左挪、
    //   可以和别的音重叠，这些老格式在数学上就表达不了。
  }

  /** 对应 Python 的 `__str__`。 */
  toString() {
    if (this.is_rest) {
      // 不带单位 —— `duration` 内部是"拍"，但界面上不出现这个字。
      return '休止' + _fmt(this.duration);
    }
    return this.pitches.join('+');
  }

  /** 对应 `@dataclass` 自动生成的 `__eq__`（逐字段比，同类才算）。 */
  equals(other) {
    return other instanceof Chord
      && _listEq(this.pitches, other.pitches)
      && this.duration === other.duration
      && this.is_rest === other.is_rest
      && this.raw === other.raw
      && this.line === other.line
      && this.col === other.col
      && Object.is(this.at, other.at);
  }
}

/** 曲子中途的变速标记。（对应 `@dataclass class BpmChange`） */
export class BpmChange {
  constructor(bpm, line = 0, col = 0) {
    if (bpm !== null && typeof bpm === 'object') {
      const opts = bpm;                     // 对象式（web 原有写法）
      this.bpm = opts.bpm;
      this.line = opts.line === undefined ? 0 : opts.line;
      this.col = opts.col === undefined ? 0 : opts.col;
      return;
    }
    this.bpm = bpm;
    this.line = line;
    this.col = col;
  }

  equals(other) {
    return other instanceof BpmChange
      && this.bpm === other.bpm
      && this.line === other.line
      && this.col === other.col;
  }
}

/** 列表相等（Python 的 `==` 是逐元素比，不是比引用）。 */
function _listEq(a, b) {
  if (a.length !== b.length) {
    return false;
  }
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) {
      return false;
    }
  }
  return true;
}

/** 一份谱子：和弦 / 变速标记的有序事件流。（对应 `@dataclass class Sheet`） */
export class Sheet {
  constructor(events = [], source = '', title = '', free = false) {
    if (events !== null && typeof events === 'object' && !Array.isArray(events)) {
      const opts = events;                  // 对象式（web 原有写法）
      this.events = opts.events === undefined ? [] : opts.events;
      this.source = opts.source === undefined ? '' : opts.source;
      this.title = opts.title === undefined ? '' : opts.title;
      this.free = opts.free === undefined ? false : opts.free;
    } else {
      this.events = events;
      this.source = source;
      this.title = title;
      this.free = free;
    }
    // ★ 是不是「时间轴谱面」★
    //   true  = 每个音自己带绝对时间（`秒:音高`），位置互相独立，
    //           可以向左挪、可以重叠。制谱器按这个决定用哪套编辑规则。
    //   false = 老格式（顺序记谱），位置靠前面的累计时值推出来。
  }

  // ---- 便捷视图 ----

  /** 对应 Python 的 `@property chords`。 */
  get chords() {
    return this.events.filter((e) => e instanceof Chord);
  }

  /** 对应 Python 的 `@property has_chords`。 */
  get has_chords() {
    return this.events.some((e) => e instanceof Chord);
  }

  /** 对应 Python 的 `__len__`。 */
  __len__() {
    return this.chords.length;
  }

  /** 对应 Python 的 `__bool__`。 */
  __bool__() {
    return this.chords.length > 0;
  }

  // ★ JS 附加（Python 里没有）★ —— JS 侧习惯了 `sheet.length`，留着方便，
  //   语义跟 `__len__()` 完全一样，不影响两边的一致性。
  get length() {
    return this.__len__();
  }

  // ★ JS 附加（web 这边的老名字，留着别删）★ = `!__bool__()`。
  get isEmpty() {
    return this.chords.length === 0;
  }

  /** 人类可读的预览，用于调试 / 界面显示。 */
  describe(limit = 0) {
    const lines = [];
    if (this.title) {
      lines.push('标题: ' + this.title);
    }
    let n = 0;
    for (const ev of this.events) {
      if (ev instanceof BpmChange) {
        lines.push('  BPM -> ' + String(ev.bpm));
      } else {
        const tag = ev.is_rest ? '休止' : ev.pitches.join('+');
        // 对应 Python 的 `'  %-12s %s 拍'`（`%s` 那边走的是 `_fmt`）
        lines.push('  ' + _padEnd12(tag) + ' ' + _fmt(ev.duration) + ' 拍');
      }
      n += 1;
      if (limit && n >= limit) {
        lines.push('  ...');
        break;
      }
    }
    return lines.join('\n');
  }
}

// ============================================================================
// 四、时值 / token 小工具
// ============================================================================

/**
 * 0.25 -> '0.25'，2.0 -> '2'（去掉多余的小数点）。
 * ★ Python↔JS ★ 里面的 `round` 是银行家舍入、`%g` 是 6 位有效数字，
 *   两个都用上面的替身，别换成 `Math.round` / `String()`。
 */
export function _fmt(x) {
  if (Math.abs(x - _pyRound0(x)) < 1e-9) {
    return _pyIntStr(_pyRound0(x));
  }
  return _pyFmtG(x);
}

/** token 是否只由白名单字符组成。 */
export function is_valid_token(token) {
  // ★ Python↔JS ★ Python 里空串返回 False、给 `None` 会 TypeError；
  //   JS 这边空串 / null / undefined 一律 False（调用方不用先判空）。
  if (!token) {
    return false;
  }
  for (const c of token) {
    if (!ALLOWED_CHARS.has(c)) {
      return false;
    }
  }
  return true;
}

/**
 * 算一个 token 的时长。返回 `[拍数, 是否休止]`（对应 Python 的 tuple）。
 *
 * 引擎核心：**先加法，后乘法**
 *     基础时长 = 有音符本体 ? 1.0 : 0.0
 *     每个 '-' 加 1.0 拍
 *     每个 '^' 把总时长砍一半
 */
export function token_duration(token) {
  const body = _stripChars(token, _STRIP_FOR_BODY);

  // ★ Python↔JS ★ Python 是 `any(c.isdigit() ...)` —— 认的是 `isdigit()`，
  //   不只是 ASCII 0-9（上标 ²、圈码 ①、阿拉伯-印度数字 ٣ 都算）。
  let has_note = false;
  for (const c of body) {
    if (_isDigitChar(c)) {
      has_note = true;
      break;
    }
  }
  let duration = has_note ? 1.0 : 0.0;

  duration += _count(token, '-');
  const carets = _count(token, '^');
  for (let k = 0; k < carets; k += 1) {
    duration *= 0.5;
  }

  return [duration, !has_note];
}

/** 把 '1&3&5' 拆成 ['1','3','5']；单音就返回单元素列表。 */
export function split_chord(pitches_text) {
  return pitches_text.split('&').filter((p) => p !== '');
}

// ============================================================================
// 五、摊平：老写法 → 绝对秒
// ============================================================================

/**
 * ★ 把整篇统一成「时间轴谱面」：位置一律是**绝对秒** ★
 *
 * 老写法（`5 3 5`、`1 - 2`）在这里被换算成秒 —— 于是「旧格式」
 * 只是一种**输入写法**，进来之后内部只有一种模型：每个音自己带
 * 绝对时间，编辑器可以随便左右拖、随便重叠。
 *
 * 为什么非得在这一层摊平：老写法的位置是"前一个音 + 时值"**推**出来的，
 * 向左挪等于要求"负间距"，而负的休止符 token 不存在 —— 数学上就做不到。
 */
export function _finalize_free(sheet) {
  // ★ 按**秒**往前走，不要按拍累加 ★
  //   拍速会被 `#BPM#` 改掉，而"这个音在第几秒"取决于**它当时**的 BPM。
  //   原来写成 `at = beat * (60/bpm)`，于是 `#60# 1 #120# 1` 里
  //   第二个音被按 120 折算成 0.5 秒 —— 而它其实在 1.0 秒
  //   （第一个音在 60 BPM 下占满 1 秒）。
  let t = 0.0;
  let bpm = DEFAULT_BPM;
  const ch = [];
  const bpms = [];
  for (const ev of sheet.events) {
    if (ev instanceof BpmChange) {
      if (ev.bpm > 0) {
        bpm = ev.bpm;
      }
      continue;
    }
    if (!(ev instanceof Chord)) {
      continue;
    }
    // ★ Python↔JS ★ Python 判的是 `ev.at is None` —— **不是** `not ev.at`：
    //   `at == 0.0` 是合法的绝对时间（第一个音就在 0 秒），不能当成"没写"。
    if (ev.at === null || ev.at === undefined) {
      ev.at = t;
    }
    // ★ Python↔JS ★ 这里的 `max` 得用 Python 语义（见 `_pyMax2`），
    //   不然 JS 的 `Math.max(1, bpm)` 在 bpm 是 NaN 时行为不同。
    t = ev.at + ev.duration * (60.0 / _pyMax2(1, bpm));
    ch.push(ev);
    bpms.push(bpm);
  }
  // 时值仍然按**拍**（`Timeline` 等下游用的就是这个单位）：
  // 拿相邻两个音的绝对秒差、按**它当时的 BPM** 折回来。
  //   ★ 别用全局 SPB ★ —— 那是 BPM 120 的系数，
  //   碰到 `#60#` 会算成"1 拍 = 2 拍"。
  //   ★ 最后一个音保留它原本的时值 ★ —— 老写法里 token 自带
  //   （`1-` 就是 2 拍）；新写法里没有"下一个音"可参照，
  //   给 1 拍收尾即可。原来无脑覆盖成 1 拍，会把 `#180# ^1 ^2 ^3`
  //   这种三连音的总长度算错。
  for (let i = 0; i < ch.length; i += 1) {
    const c = ch[i];
    if (i + 1 < ch.length) {
      const spb = 60.0 / _pyMax2(1, bpms[i]);
      // ★ Python↔JS ★ 又是 `max(0.0, ...)`：谱面里写 `nan:5` 时
      //   Python 给 0.0、JS 的 `Math.max` 会给 NaN —— 必须走 `_pyMax2`。
      c.duration = _pyMax2(0.0, (ch[i + 1].at - c.at) / spb);
    } else if (c.duration <= 0) {
      c.duration = 1.0;
    }
  }
  sheet.free = true;
}

/** #120# 这种形式的变速标记。 */
export function _is_bpm_token(token) {
  // ★ Python↔JS ★ `len(token)` 数的是码点，所以用 `_codePointLen`，别用 `.length`。
  return token.startsWith('#') && token.endsWith('#') && _codePointLen(token) > 2;
}

// ============================================================================
// 六、解析
// ============================================================================

/** 有没有任何一个字符在白名单里（对应 `any(c in ALLOWED_CHARS for c in s)`）。 */
function _hasAllowedChar(s) {
  for (const c of s) {
    if (ALLOWED_CHARS.has(c)) {
      return true;
    }
  }
  return false;
}

/**
 * 把记谱文本解析成 Sheet。
 *
 * 规则与猫弹琴的 _parse_score_with_positions 一致：
 *   * 按空白切 token
 *   * `#数字#` 是变速标记
 *   * 纯 `^` 的 token 攒成前缀，拼到下一个音符前面
 *   * 非法字符的 token 整段跳过
 *   * 非乐谱文字跳过且**不留时间空档**
 */
export function parse(text) {
  const sheet = new Sheet([], text);        // 对应 Python 的 Sheet(source=text)
  const events = sheet.events;

  let pending_prefix = '';
  let line = 0;
  let col = 0;
  let i = 0;
  // ★ Python↔JS ★ Python 的字符串按**码点**索引，JS 按 UTF-16 码元。
  //   列号（`Chord.col`）和 token 切片都得跟 Python 一模一样，所以先把文本
  //   摊成码点数组，后面全按码点走 —— 谱面里写了 emoji 时才看得出区别
  //   （一个 emoji 在 JS 里占 2 个码元、在 Python 里是 1 个字符）。
  const chars = Array.from(text);
  const length = chars.length;

  // 标题：第一行里不含乐谱字符的整行文本
  for (const raw_line of _splitlines(text)) {
    const stripped = _stripPy(raw_line);
    if (stripped && !_hasAllowedChar(stripped)) {
      sheet.title = stripped;
      break;
    }
  }

  while (i < length) {
    // --- 跳过空白，同时维护行列号 ---
    // ★ 只认这四种 ★ 换行重置列号，其余三个只加列号。
    //   `\v` `\f` `\xa0` 这些 Python 也不当空白 —— 它们会被连进 token 里，
    //   然后因为不在白名单而整段丢掉。
    while (i < length) {
      const c = chars[i];
      if (c === '\n') {
        line += 1;
        col = 0;
        i += 1;
      } else if (c === ' ' || c === '\t' || c === '\r') {
        col += 1;
        i += 1;
      } else {
        break;
      }
    }

    if (i >= length) {
      break;
    }

    const start_line = line;
    const start_col = col;
    const start_index = i;

    // --- 读一个 token（到空白为止）---
    while (i < length && !_isTokenSpace(chars[i])) {
      i += 1;
      col += 1;
    }

    const token = chars.slice(start_index, i).join('');

    // --- 变速标记 ---
    if (_is_bpm_token(token)) {
      pending_prefix = '';
      const bpm_str = token.slice(1, -1);
      if (_pyIsDigit(bpm_str)) {
        const bpm = _pyInt(bpm_str);
        if (bpm === null) {
          // ★ 这条是**照抄 Python 的行为**，不是本移植版新加的 ★
          //   `isdigit()` 认圈码数字（'①' 算数字），而 `int()` 只认十进制数字，
          //   于是 Python 的 `parse('#①# 1')` 直接抛 ValueError。
          //   两边都抛 = 行为一致；要改成"忽略这个标记"的话，**两边一起改**。
          throw new ValueError(`invalid literal for int() with base 10: '${bpm_str}'`);
        }
        events.push(new BpmChange(bpm, start_line, start_col));
      }
      continue;
    }

    // --- ★ 时间轴格式：`秒:音高` ★ ---
    //   例：`0:5 0.25:3 1:1'&3' 2.5:2'`
    //   冒号左边是**绝对开始时间（秒）**，右边还是老记谱法的音高。
    //   一旦出现这种 token，整篇就按"时间轴谱面"处理（`sheet.free`）——
    //   每个音的位置自己说了算，不再由前面的音累加推算。
    //   放在 `is_valid_token` 之前，因为 `:` 不在白名单里。
    if (token.indexOf(':') !== -1) {
      // `head, _, tail = token.partition(':')`
      const [head, , tail] = _pyPartition(token, ':');
      // ★ Python↔JS ★ Python 是 `try: at = float(head) except ValueError: at = None`；
      //   `_pyFloat` 失败时给 null，语义一样。
      //   ★ 判的是 `!== null`、不是真值 ★ —— `0:5` 的 0.0 是合法时间，不能丢。
      const at = _pyFloat(head);
      if (at !== null) {
        sheet.free = true;
        pending_prefix = '';
        const body = _stripChars(tail, ['^', '-', '~']);
        const pitches = split_chord(body);
        if (pitches.length) {
          events.push(new Chord(pitches, 0.0, false, token,
                                start_line, start_col, at));
        }
        continue;
      }
    }

    // --- 合法性校验：非法就整段跳过，不留空档 ---
    if (!is_valid_token(token)) {
      pending_prefix = '';
      continue;
    }

    // --- 纯 '^' 的 token 攒起来当前缀 ---
    if (_stripChars(token, ['^']) === '') {
      pending_prefix += token;
      continue;
    }

    const full = pending_prefix + token;
    pending_prefix = '';

    const [duration, is_rest] = token_duration(full);

    const body = _stripChars(full, ['^', '-', '~']);
    const pitches = split_chord(body);

    events.push(new Chord(pitches, duration, is_rest, full,
                          start_line, start_col));
  }

  // ★ 统一成绝对时间（时间轴谱面）★
  //   老写法在这里被换算成秒，之后内部只有一种模型。
  _finalize_free(sheet);
  return sheet;
}

/** token 的分隔空白（Python 里就是 `' \t\n\r'` 这四个字符）。 */
function _isTokenSpace(c) {
  return c === ' ' || c === '\t' || c === '\n' || c === '\r';
}

// ============================================================================
// 七、文件读写
//   ★ 这一节是 web 适配，跟 Python 版差得最多，见注释 ★
//   网页版现在读写谱面走的是 `store.js`（localStorage + 上传下载），
//   这两个函数是给"从服务器上的 sheets/ 直接读 / 存回文件"这类用法准备的，
//   名字跟 `core/parser.py` 的 `load` / `save` 对齐，方便后面接着移植。
// ============================================================================

/**
 * 从文件读一份谱子（自动尝试 UTF-8 / GBK）。
 *
 * ★ Python↔JS ★ Python 版收路径、**同步**开文件；浏览器里没有文件系统，
 *   所以这里是 **async**，而且能收三种东西：
 *     * URL / 路径字符串 —— `fetch()` 取（网页版的正常用法）
 *     * `File` / `Blob`      —— 手机上选文件拿到的东西
 *     * `ArrayBuffer` / `Uint8Array` —— 字节已经在手上时直接用
 *   编码尝试顺序、以及"解不开就试下一个"的行为跟 Python 一模一样。
 */
export async function load(source) {
  const buf = await _read_bytes(source);
  // Python: for enc in ('utf-8-sig', 'utf-8', 'gbk', 'utf-16')
  for (const enc of ['utf-8-sig', 'utf-8', 'gbk', 'utf-16']) {
    let text;
    try {
      text = _decode(buf, enc);
    } catch (err) {
      continue;                             // = Python 的 UnicodeDecodeError
    }
    // ★ parse 放在 try 外面 ★ Python 只在解码那一步 catch，
    //   解析自己的错（比如 `#①#`）照旧往外抛，别顺手吞掉。
    return parse(text);
  }
  return parse(_decode(buf, 'utf-8-replace'));
}

/** 把 text 写成谱面文件（UTF-8 + CRLF）。 */
export async function save(path, text) {
  // Python: open(path, 'w', encoding='utf-8', newline='\r\n')
  //   `newline='\r\n'` 的效果就是"每个 \n 写成 \r\n"（原有的 \r\n 会再插一个 \r，
  //   这是 Python 的行为，照抄）。
  const data = text.split('\n').join('\r\n');
  if (path && typeof path.createWritable === 'function') {
    // File System Access API 的文件句柄（手机上也能用）
    const w = await path.createWritable();
    await w.write(data);
    await w.close();
    return;
  }
  // 浏览器里唯一"存到本地"的老办法：触发一次下载
  const name = typeof path === 'string'
    ? (path.split(/[\\/]/).pop() || 'sheet.txt') : 'sheet.txt';
  const blob = new Blob([data], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** 把 `load()` 收的几种东西统一成 ArrayBuffer。 */
async function _read_bytes(source) {
  if (typeof source === 'string') {
    const res = await fetch(source);
    if (!res.ok) {
      throw new Error(`读取谱面失败：HTTP ${res.status} ${source}`);
    }
    return res.arrayBuffer();
  }
  if (source instanceof ArrayBuffer) {
    return source;
  }
  if (ArrayBuffer.isView(source)) {
    return source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);
  }
  if (typeof source.arrayBuffer === 'function') {   // Blob / File
    return source.arrayBuffer();
  }
  throw new TypeError('load() 只收 URL 字符串 / File / Blob / ArrayBuffer');
}

/**
 * ★ Python↔JS ★ 浏览器里的 `'gbk'` **其实就是 gb18030**（Encoding Standard 把 gbk
 * 这个标签指到 gb18030 解码器上），比 Python 的 `'gbk'` 宽：
 *   `[0x80, 0x81, 0x82]` 这种在 Python 里是 UnicodeDecodeError，在浏览器里却解得出字符。
 * 于是 Python 那边会继续往下试 utf-16 / utf-8+replace，而这边的结果就不一样了
 * —— 明明两边都"没报错"，谱面却不一样。
 *
 * 所以先按 CP936/GBK 的**字节结构**筛一遍（Python 的 gbk 认的就是这个结构）：
 *   单字节 0x00~0x7F；双字节 = lead 0x81~0xFE + trail 0x40~0x7E 或 0x80~0xFE。
 * 结构不对就当"解不开"，交给调用方去试下一个编码 —— 跟 Python 抛
 * UnicodeDecodeError 的效果一样。
 *
 * ★ 还剩一处对不齐，认了 ★
 *   还有 2149 个双字节组合是"Python 的 cp936 表里没定义、浏览器的 gb18030 表里有"
 *   （扫全部 23940 个双字节组合量出来的：两边都定义的位置上码点**零差异**，
 *   这 2149 个里 2048 个落在私用区 PUA、101 个是真字符）。
 *   要补齐就得把那张表抄进来，代价远大于收益 —— 只有文件里真出现这些
 *   "CP936 未定义位"的字节时才会分岔，正常 GBK 中文谱面一个都碰不到。
 *   （对拍脚本 `_port_check_parser.py gbkdiff` 能把这 2149 个原样打出来。）
 */
function _gbk_structurally_ok(bytes) {
  let i = 0;
  while (i < bytes.length) {
    const b = bytes[i];
    if (b <= 0x7f) {
      i += 1;
      continue;
    }
    if (b === 0x80 || b === 0xff) {
      return false;                         // 这两个字节在 GBK 里没定义
    }
    if (i + 1 >= bytes.length) {
      return false;                         // 落单的 lead
    }
    const t = bytes[i + 1];
    if (!((t >= 0x40 && t <= 0x7e) || (t >= 0x80 && t <= 0xfe))) {
      return false;                         // trail 落在 0x7F / 0xFF 上
    }
    i += 2;
  }
  return true;
}

/** 按 Python 的编码名解码，失败抛错（对应 Python 的 UnicodeDecodeError）。 */
function _decode(buf, enc) {
  const bytes = new Uint8Array(buf);
  switch (enc) {
    case 'utf-8-sig':
      // 跟 Python 的 utf-8-sig 一样：先吃掉可有可无的 BOM，其余按 UTF-8 严格解。
      // TextDecoder 默认（ignoreBOM: false）就会把 BOM 去掉。
      return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    case 'utf-8':
      // Python 的 'utf-8' **不**吃 BOM（会留一个 \ufeff）—— 用 ignoreBOM 对齐。
      return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
    case 'gbk':
      // 见 `_gbk_structurally_ok`：先把 Python 会拒绝的字节序列挡掉
      if (!_gbk_structurally_ok(bytes)) {
        throw new Error('gbk: 不是合法的 GBK 字节序列');
      }
      return new TextDecoder('gbk', { fatal: true }).decode(bytes);
    case 'utf-16':
      // Python 的 'utf-16'：有 BOM 按 BOM，没 BOM 按本机字节序（Windows = 小端）。
      // Encoding Standard 里的 'utf-16' 只有小端，所以这里自己嗅一下 BOM。
      if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
        return new TextDecoder('utf-16le', { ignoreBOM: true }).decode(bytes.subarray(2));
      }
      if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
        return new TextDecoder('utf-16be', { ignoreBOM: true }).decode(bytes.subarray(2));
      }
      return new TextDecoder('utf-16le', { fatal: true, ignoreBOM: true }).decode(bytes);
    default:
      // Python: raw.decode('utf-8', 'replace') —— 兜底，永不失败
      return new TextDecoder('utf-8').decode(bytes);
  }
}

// ============================================================================
// 八、导出清单（跟 `core/parser.py` 的模块级名字一一对应）
//   Python 里所有模块级名字都能 import，所以这里连 `_` 开头的也一起导出
//   —— 对拍脚本要直接调它们。
// ============================================================================

export default {
  ALLOWED_CHARS,
  _STRIP_FOR_BODY,
  DEFAULT_BPM,
  SPB,
  ValueError,
  OverflowError,
  Chord,
  BpmChange,
  Sheet,
  _fmt,
  is_valid_token,
  token_duration,
  split_chord,
  _finalize_free,
  _is_bpm_token,
  parse,
  load,
  save,
  // Python 内建替身（对拍 / 测试用）
  _stripPy,
  _pyIsSpace,
  _isDigitChar,
  _pyIsDigit,
  _pyFloat,
  _pyInt,
  _pyIntStr,
  _pyRound0,
  _pyFmtG,
  _pyMax2,
  _count,
  _stripChars,
  _splitlines,
  _pyPartition,
  _codePointLen,
  _padEnd12,
};
