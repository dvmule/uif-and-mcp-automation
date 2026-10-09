// Echo's remembered rail state ('1' = collapsed), read so this app opens the same way; see ui/src/lib/railPreference.ts.
export const RAIL_KEY = "relay.echo.rail.collapsed";

export function readRailCollapsed(storage?: Pick<Storage, "getItem">): boolean {
  try {
    return (storage ?? window.localStorage).getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}
