// 游戏里那台琴的 16 个键位 —— 布局与音高映射。
//
// 排列（跟游戏画面一致，行 0 是最下面一行）：
//
//     行3（上） PAD13(5')  PAD14(6')  PAD15(7')  PAD16(1'')
//     行2       PAD9 (1')  PAD10(2')  PAD11(3')  PAD12(4')
//     行1       PAD5 (5)   PAD6 (6)   PAD7 (7)   PAD8 (8)
//     行0（下） PAD1 (1)   PAD2 (2)   PAD3 (3)   PAD4 (4)
//
// PAD 编号 = 行号 × 4 + 列号 + 1。
//
// 注意：`8` 和 `1'` 在乐理上是同一个音高（高音 do），游戏里却是两个独立的键。
// 本程序按**标签精确匹配** —— 谱面写 `8` 就指向 PAD8，写 `1'` 就指向 PAD9。
// 若实际打下来发现对不上，改这张表即可（就是下面这个 PAD_GRID）。
//
// （这份是从 `core/layout.py` 移植的，两边行为必须一致。）

export const PAD_ROWS = 4;
export const PAD_COLS = 4;
export const PAD_COUNT = PAD_ROWS * PAD_COLS;

// ★ 唯一的真相来源：改这里就能改键位映射 ★
export const PAD_GRID = [
  ['1', '2', '3', '4'],            // 行 0（最下）
  ['5', '6', '7', '8'],            // 行 1
  ["1'", "2'", "3'", "4'"],        // 行 2
  ["5'", "6'", "7'", "1''"],       // 行 3（最上）
];

// ★ 游戏里每个键面上印的是 "PAD N"，不是简谱音名 ★
//   浮窗上两个都标：音名（谱面用）+ PAD 号（游戏里对着找键用）。
export const PAD_LABELS = (() => {
  const out = [];
  for (let r = 0; r < PAD_ROWS; r++) {
    const row = [];
    for (let c = 0; c < PAD_COLS; c++) {
      row.push('PAD ' + (r * PAD_COLS + c + 1));
    }
    out.push(row);
  }
  return out;
})();

/** 格子坐标 -> 游戏里的 PAD 编号（1~16）。 */
export function pad_number(row, col) {
  return row * PAD_COLS + col + 1;
}

/** 格子坐标 -> 音高标签。 */
export function cell_to_pitch(row, col) {
  return PAD_GRID[row][col];
}

/** 音高标签 -> [行, 列]。琴上没这个音就返回 null。 */
export function pitch_to_cell(pitch) {
  for (let r = 0; r < PAD_ROWS; r++) {
    for (let c = 0; c < PAD_COLS; c++) {
      if (PAD_GRID[r][c] === pitch) return [r, c];
    }
  }
  return null;
}

/** 音高标签 -> PAD 编号（1~16）。找不到返回 null。 */
export function pitch_to_pad(pitch) {
  const cell = pitch_to_cell(pitch);
  if (cell === null) return null;
  return pad_number(cell[0], cell[1]);
}

/** 琴上所有音高，按 PAD 编号顺序。 */
export function all_pitches() {
  const out = [];
  for (let r = 0; r < PAD_ROWS; r++) {
    for (let c = 0; c < PAD_COLS; c++) out.push(cell_to_pitch(r, c));
  }
  return out;
}

/** 在一堆音高里，找出琴上弹不出来的那些（去重保序）。 */
export function unmapped_pitches(pitches) {
  const seen = new Set();
  const out = [];
  for (const p of pitches) {
    if (!seen.has(p) && pitch_to_cell(p) === null) {
      seen.add(p);
      out.push(p);
    }
  }
  return out;
}
