// Line diff for the Review step's "exactly what changes" view (UIFPE-219).
// Display-only by design: the texts diffed here are the compiler's own
// before/after — this module never builds SQL, it only shows the
// difference between two strings the authority produced.
//
// Exact LCS on the changed middle (common prefix/suffix trimmed first),
// which is precise and fast at gold-SQL sizes; past the size cap it
// degrades to replace-everything rather than freezing the tab.

export interface DiffRow {
  kind: "same" | "add" | "del";
  text: string;
  /** 1-based line number in the BEFORE text (null for added lines). */
  a: number | null;
  /** 1-based line number in the AFTER text (null for deleted lines). */
  b: number | null;
}

const LCS_CELL_CAP = 4_000_000; // ~2000×2000 changed lines — far past any artifact

function lcsRows(as: string[], bs: string[],
                 aOff: number, bOff: number): DiffRow[] {
  const n = as.length, m = bs.length;
  if (!n && !m) return [];
  const rows: DiffRow[] = [];
  if (!n || !m || n * m > LCS_CELL_CAP) {
    for (let i = 0; i < n; i++)
      rows.push({ kind: "del", text: as[i], a: aOff + i + 1, b: null });
    for (let j = 0; j < m; j++)
      rows.push({ kind: "add", text: bs[j], a: null, b: bOff + j + 1 });
    return rows;
  }
  // DP over (n+1)×(m+1); Int32Array keeps 4M cells comfortably small.
  const w = m + 1;
  const dp = new Int32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i * w + j] = as[i] === bs[j]
        ? dp[(i + 1) * w + j + 1] + 1
        : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (as[i] === bs[j]) {
      rows.push({ kind: "same", text: as[i], a: aOff + i + 1, b: bOff + j + 1 });
      i++; j++;
    } else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) {
      rows.push({ kind: "del", text: as[i], a: aOff + i + 1, b: null });
      i++;
    } else {
      rows.push({ kind: "add", text: bs[j], a: null, b: bOff + j + 1 });
      j++;
    }
  }
  for (; i < n; i++) rows.push({ kind: "del", text: as[i], a: aOff + i + 1, b: null });
  for (; j < m; j++) rows.push({ kind: "add", text: bs[j], a: null, b: bOff + j + 1 });
  return rows;
}

export function diffLines(before: string, after: string): DiffRow[] {
  const a = before.split("\n"), b = after.split("\n");
  let lo = 0;
  const max = Math.min(a.length, b.length);
  while (lo < max && a[lo] === b[lo]) lo++;
  let aHi = a.length, bHi = b.length;
  while (aHi > lo && bHi > lo && a[aHi - 1] === b[bHi - 1]) { aHi--; bHi--; }
  const rows: DiffRow[] = [];
  for (let i = 0; i < lo; i++)
    rows.push({ kind: "same", text: a[i], a: i + 1, b: i + 1 });
  rows.push(...lcsRows(a.slice(lo, aHi), b.slice(lo, bHi), lo, lo));
  const shift = b.length - a.length;
  for (let i = aHi; i < a.length; i++)
    rows.push({ kind: "same", text: a[i], a: i + 1, b: i + 1 + shift });
  return rows;
}

export interface DiffHunk {
  rows: DiffRow[];
  /** Unchanged lines elided since the previous hunk (or the file start). */
  skippedBefore: number;
}

export interface DiffStats { added: number; removed: number }

export function diffStats(rows: DiffRow[]): DiffStats {
  let added = 0, removed = 0;
  for (const r of rows) {
    if (r.kind === "add") added++;
    else if (r.kind === "del") removed++;
  }
  return { added, removed };
}

/** Changed regions with `context` unchanged lines around each; [] when the
 *  texts are identical. Adjacent regions whose context would touch merge
 *  into one hunk. */
export function diffHunks(before: string, after: string,
                          context = 3): DiffHunk[] {
  const rows = diffLines(before, after);
  const changed = rows.map((r) => r.kind !== "same");
  if (!changed.some(Boolean)) return [];
  const keep = new Array<boolean>(rows.length).fill(false);
  for (let i = 0; i < rows.length; i++) {
    if (!changed[i]) continue;
    for (let k = Math.max(0, i - context);
         k <= Math.min(rows.length - 1, i + context); k++) keep[k] = true;
  }
  const hunks: DiffHunk[] = [];
  let current: DiffRow[] | null = null;
  let skipped = 0;
  for (let i = 0; i < rows.length; i++) {
    if (keep[i]) {
      if (!current) {
        current = [];
        hunks.push({ rows: current, skippedBefore: skipped });
        skipped = 0;
      }
      current.push(rows[i]);
    } else {
      current = null;
      skipped++;
    }
  }
  return hunks;
}
