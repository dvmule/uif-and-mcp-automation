// multiSelect.js (UIFPE-693)
// The rules behind the shared multi-select dropdown (MultiSelect.jsx): what the
// closed box says, which options a search leaves, and what "Select all" does.
// Pure functions so the wording and the toggle rules are pinned by vitest.
//
// items    [{ key, label }]
// selected an array of keys (order does not matter)

/** What the closed box reads: one pick by name, several as a count, all as
 *  "All <noun> (n)", none as the placeholder. */
export function selectionLabel(items, selected, noun, placeholder = "Choose…") {
  const picked = items.filter((it) => selected.includes(it.key));
  if (picked.length === 0) return { text: placeholder, empty: true };
  if (picked.length === 1) return { text: picked[0].label, empty: false };
  if (picked.length === items.length) return { text: `All ${noun} (${items.length})`, empty: false };
  return { text: `${picked.length} ${noun} selected`, of: items.length, empty: false };
}

/** Options whose label contains the search text, ignoring case and edge spaces. */
export function filterItems(items, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return items;
  return items.filter((it) => String(it.label).toLowerCase().includes(q));
}

/** True when every option is picked (and there is at least one option). */
export function allSelected(items, selected) {
  return items.length > 0 && items.every((it) => selected.includes(it.key));
}

/** "Select all" when anything is missing, "Clear all" when everything is picked.
 *  Keys that are not among `items` are left as they were. */
export function toggleAll(items, selected) {
  const keys = items.map((it) => it.key);
  if (allSelected(items, selected)) return selected.filter((k) => !keys.includes(k));
  return [...new Set([...selected, ...keys])];
}

/** One option on or off. */
export function toggleOne(selected, key) {
  return selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key];
}
