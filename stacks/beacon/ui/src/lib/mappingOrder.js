// mappingOrder.js (UIFPE-652)
// Reordering the mapping rows by drag: where a dragged row lands, the new
// list, and whether the drop must be confirmed first. Pure; Wizard.jsx renders
// what these return. The order of `mappings` IS the file's column order, so a
// move on a published export is a partner-visible change — hence the ask.

/** Where a pointer over row `index` would drop: before it (upper half) or
 *  after it (lower half). `y`, `top` and `height` come from the row's box. */
export function dropIndex(index, y, top, height) {
  return y < top + height / 2 ? index : index + 1;
}

/** The list with the row at `from` moved so that it sits at insertion point
 *  `to` (0 … length, measured on the list BEFORE the row is taken out).
 *  Returns the same array when nothing would change. */
export function moveRow(list, from, to) {
  if (!Array.isArray(list)) return list;
  if (from == null || to == null || from < 0 || from >= list.length) return list;
  if (to < 0 || to > list.length) return list;
  if (to === from || to === from + 1) return list;
  const next = list.slice();
  const [row] = next.splice(from, 1);
  next.splice(to > from ? to - 1 : to, 0, row);
  return next;
}

/** A move needs the owner's yes when the export is already published (we are
 *  editing) and this editing session has not been asked yet. */
export function reorderNeedsConfirm({ editing, asked }) {
  return Boolean(editing) && !asked;
}
