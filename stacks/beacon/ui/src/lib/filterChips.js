// filterChips.js (UIFPE-762)
// The rules behind FilterChips.jsx — the list toolbars' "one chip per active
// filter" bar. Pure, so the wording and the active/default rules are pinned
// by vitest.
//
// A filter definition is one of:
//   { key, name, kind: "multi",  items: [{key,label}], selected: [keys], onChange(keys), start?: [keys], noun? }
//   { key, name, kind: "single", items: [{key,label}], value, defaultValue, onChange(key), start?: key }
//   { key, name, kind: "toggle", on, onChange(bool), onText? }
// A filter at its default has no chip: every option on, the default value, or off.

/** What a chip reads after its name: the picked labels, at most `max` of
 *  them, then "+N"; "none" when nothing is picked. */
export function chipValueText(labels, max = 2) {
  if (!labels.length) return "none";
  if (labels.length <= max) return labels.join(", ");
  return `${labels.slice(0, max).join(", ")} +${labels.length - max}`;
}

/** True when the filter narrows the list — i.e. it gets a chip. */
export function isActive(def) {
  if (def.kind === "multi") return def.items.length > 0 && def.selected.length < def.items.length;
  if (def.kind === "single") return def.value !== def.defaultValue;
  return Boolean(def.on);
}

/** The value that puts the filter back to its default (removes the chip). */
export function resetValue(def) {
  if (def.kind === "multi") return def.items.map((it) => it.key);
  if (def.kind === "single") return def.defaultValue;
  return false;
}

/** The value a filter starts with when picked from "+ Filter": the caller's
 *  `start` when it exists among the options, else the first option (for a
 *  single, the first option that is not the default). */
export function startValue(def) {
  if (def.kind === "toggle") return true;
  const keys = def.items.map((it) => it.key);
  if (def.kind === "multi") {
    const wanted = (def.start || []).filter((k) => keys.includes(k));
    return wanted.length ? wanted : keys.slice(0, 1);
  }
  if (def.start !== undefined && keys.includes(def.start)) return def.start;
  return keys.find((k) => k !== def.defaultValue) ?? def.defaultValue;
}

/** Filters that can be offered at all — a multi with no options yet (the
 *  list is still loading) is left out rather than shown as a dead entry. */
export function offerable(defs) {
  return defs.filter((d) => d.kind !== "multi" || d.items.length > 0);
}
