// UIFPE-637: the collapsed/expanded choice for the left menu, remembered per
// browser and never able to break the menu when storage is unavailable.
import { describe, expect, it } from "vitest";
import { RAIL_KEY, readRailCollapsed, writeRailCollapsed } from "../railPreference.js";

function memoryStorage() {
  const data = {};
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    data,
  };
}

describe("railPreference", () => {
  it("starts expanded when nothing is stored", () => {
    expect(readRailCollapsed(memoryStorage())).toBe(false);
  });

  it("remembers collapsed and expanded", () => {
    const s = memoryStorage();
    writeRailCollapsed(s, true);
    expect(s.data[RAIL_KEY]).toBe("1");
    expect(readRailCollapsed(s)).toBe(true);
    writeRailCollapsed(s, false);
    expect(readRailCollapsed(s)).toBe(false);
  });

  it("falls back to expanded when storage is missing or throws", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readRailCollapsed(undefined)).toBe(false);
    expect(readRailCollapsed(broken)).toBe(false);
    expect(() => writeRailCollapsed(broken, true)).not.toThrow();
  });
});
