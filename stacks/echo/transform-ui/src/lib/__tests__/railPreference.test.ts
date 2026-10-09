import { describe, it, expect } from "vitest";
import { RAIL_KEY, readRailCollapsed } from "../railPreference";

const store = (v: string | null) => ({ getItem: (k: string) => (k === RAIL_KEY ? v : null) });

describe("readRailCollapsed", () => {
  it("reads Echo's key: '1' is collapsed, anything else expanded", () => {
    expect(RAIL_KEY).toBe("relay.echo.rail.collapsed");
    expect(readRailCollapsed(store("1"))).toBe(true);
    expect(readRailCollapsed(store("0"))).toBe(false);
    expect(readRailCollapsed(store(null))).toBe(false);
  });

  it("falls back to expanded when storage throws or is missing", () => {
    expect(readRailCollapsed({ getItem: () => { throw new Error("blocked"); } })).toBe(false);
    expect(readRailCollapsed()).toBe(false);
  });
});
