// railPreference.js (UIFPE-637)
// Whether the left menu is collapsed to icons, remembered in this browser.
// Storage can be missing or throw (private windows, blocked site data), so
// every read and write is guarded and the menu falls back to expanded.

export const RAIL_KEY = "beacon.rail.collapsed";

export function readRailCollapsed(storage) {
  try {
    return storage?.getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeRailCollapsed(storage, collapsed) {
  try {
    storage?.setItem(RAIL_KEY, collapsed ? "1" : "0");
  } catch {
    // not remembered — the menu still works for this visit
  }
}
