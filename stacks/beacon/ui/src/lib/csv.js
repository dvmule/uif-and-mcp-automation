// csv.js (UIFPE-759)
// The one way Beacon writes a table as CSV — Insights → Scheduled jobs and
// Jobs, and the Exports list. Every cell is quoted; a cell that a spreadsheet
// would read as a formula (=, +, @, or a - that is not a number) gets a
// leading apostrophe so opening the file never runs anything.

export function csvCell(value) {
  let s = value === null || value === undefined ? "" : String(value);
  if (/^[=+@\t\r]/.test(s) || /^-(?!\d)/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

/** rows: an array of arrays (the first is the header row). */
export function toCsv(rows) {
  return (rows || []).map((row) => row.map(csvCell).join(",")).join("\n");
}

/** Save `rows` as `filename` through a temporary link (browser only). */
export function downloadCsv(filename, rows) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv" }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
