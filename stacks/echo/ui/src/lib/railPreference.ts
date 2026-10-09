// Whether the left menu is collapsed to icons, remembered in this browser.
// Storage can be missing or throw (private windows, blocked site data), so
// every read and write is guarded and the menu falls back to expanded.

export const RAIL_KEY = 'relay.echo.rail.collapsed'

export function browserStorage(): Storage | undefined {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

export function readRailCollapsed(storage: Pick<Storage, 'getItem'> | undefined): boolean {
  try {
    return storage?.getItem(RAIL_KEY) === '1'
  } catch {
    return false
  }
}

export function writeRailCollapsed(
  storage: Pick<Storage, 'setItem'> | undefined,
  collapsed: boolean,
): void {
  try {
    storage?.setItem(RAIL_KEY, collapsed ? '1' : '0')
  } catch {
    // not remembered; the menu still works for this visit
  }
}
