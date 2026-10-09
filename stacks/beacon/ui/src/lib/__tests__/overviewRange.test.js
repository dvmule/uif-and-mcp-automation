// UIFPE-610: the Overview's time range and how its counts are classified.
import { describe, expect, it } from "vitest";
import {
  DEFAULT_RANGE,
  PRESETS,
  localDay,
  outcomeCounts,
  publishedInWindow,
  rangeFromParams,
  rangeToParams,
  rangeWindow,
  successRateOf,
} from "../overviewRange.js";

const NOW = new Date(2026, 8, 25, 12, 0, 0); // Sep 25 2026, 12:00 local

describe("rangeWindow — quick picks", () => {
  it("defaults to the last 7 days ending now", () => {
    const w = rangeWindow(DEFAULT_RANGE, NOW);
    expect(w.label).toBe("7 days");
    expect(w.until).toBe(NOW.toISOString());
    expect((new Date(w.until) - new Date(w.since)) / 864e5).toBe(7);
  });

  it("covers every preset", () => {
    for (const p of PRESETS) {
      const w = rangeWindow({ kind: "preset", id: p.id }, NOW);
      expect((new Date(w.until) - new Date(w.since)) / 864e5).toBe(p.days);
      expect(w.label).toBe(p.label);
    }
  });

  it("an unknown preset falls back to 7 days", () => {
    expect(rangeWindow({ kind: "preset", id: "nope" }, NOW).label).toBe("7 days");
    expect(rangeWindow(null, NOW).label).toBe("7 days");
  });
});

describe("rangeWindow — custom", () => {
  it("counts whole local days, both ends included", () => {
    const w = rangeWindow({ kind: "custom", from: "2026-09-01", to: "2026-09-20" }, NOW);
    expect(w.since).toBe(new Date(2026, 8, 1).toISOString());
    expect(w.until).toBe(new Date(2026, 8, 21).toISOString());
    expect(w.label).toBe("Sep 1 – Sep 20");
  });

  it("one day is a 24-hour window labelled by that day", () => {
    const w = rangeWindow({ kind: "custom", from: "2026-09-15", to: "2026-09-15" }, NOW);
    expect(w.label).toBe("Sep 15");
    expect(w.until).toBe(new Date(2026, 8, 16).toISOString());
  });

  it("today as the end day is allowed", () => {
    expect(rangeWindow({ kind: "custom", from: "2026-09-20", to: "2026-09-25" }, NOW).error).toBeUndefined();
  });

  it("refuses what the API would refuse, in plain words", () => {
    const err = (from, to) => rangeWindow({ kind: "custom", from, to }, NOW).error;
    expect(err("", "2026-09-20")).toMatch(/both/);
    expect(err("2026-02-30", "2026-03-01")).toMatch(/both/); // not a real day
    expect(err("2026-09-20", "2026-09-01")).toMatch(/on or before/);
    expect(err("2026-10-01", "2026-10-05")).toMatch(/future/);
    expect(err("2024-01-01", "2026-09-01")).toMatch(/366/);
  });

  it("crosses a month and a year boundary", () => {
    const w = rangeWindow({ kind: "custom", from: "2025-12-30", to: "2026-01-02" }, NOW);
    expect(w.label).toBe("Dec 30 – Jan 2");
    expect(w.until).toBe(new Date(2026, 0, 3).toISOString());
  });
});

describe("localDay", () => {
  it("formats in the viewer's timezone", () => {
    expect(localDay(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});

describe("outcomeCounts", () => {
  it("uses the export page's rules: neutral apart, unreadable counts as failed", () => {
    const counts = outcomeCounts({
      succeeded: 40, SUCCEEDED: 1, failed: 3, unknown: 1,
      stopped: 2, skipped: 1, missed: 1, running: 1,
    });
    expect(counts).toEqual({ succeeded: 41, failed: 4, neutral: 4, total: 50 });
  });

  it("is all zero for nothing", () => {
    expect(outcomeCounts(undefined)).toEqual({ succeeded: 0, failed: 0, neutral: 0, total: 0 });
  });
});

describe("successRateOf", () => {
  it("leaves neutral runs out", () => {
    expect(successRateOf({ succeeded: 3, failed: 1, neutral: 10, total: 14 })).toBe(75);
  });

  it("is null when nothing finished", () => {
    expect(successRateOf({ succeeded: 0, failed: 0, neutral: 2, total: 2 })).toBeNull();
  });
});

describe("the range in a URL (UIFPE-754)", () => {
  it("writes a quick pick as ?range= and a custom window as ?from&to", () => {
    expect(rangeToParams({ kind: "preset", id: "30d" })).toEqual({ range: "30d" });
    expect(rangeToParams({ kind: "custom", from: "2026-09-01", to: "2026-09-20" })).toEqual({ from: "2026-09-01", to: "2026-09-20" });
    expect(rangeToParams(undefined)).toEqual({ range: "7d" });
  });

  it("reads both forms back, and ignores nonsense", () => {
    expect(rangeFromParams(new URLSearchParams("range=24h"))).toEqual({ kind: "preset", id: "24h" });
    expect(rangeFromParams(new URLSearchParams("from=2026-09-01&to=2026-09-20"))).toEqual({ kind: "custom", from: "2026-09-01", to: "2026-09-20" });
    expect(rangeFromParams(new URLSearchParams("range=2y"))).toBeNull();
    expect(rangeFromParams(new URLSearchParams("from=2026-09-20&to=2026-09-01"))).toBeNull();
    expect(rangeFromParams(new URLSearchParams("from=2026-02-30&to=2026-03-01"))).toBeNull();
    expect(rangeFromParams(new URLSearchParams(""))).toBeNull();
    expect(rangeFromParams(null)).toBeNull();
  });

  it("round-trips through rangeWindow", () => {
    const r = rangeFromParams(new URLSearchParams(rangeToParams({ kind: "preset", id: "7d" })));
    expect(rangeWindow(r, NOW).label).toBe("7 days");
  });
});

describe("publishedInWindow (UIFPE-754)", () => {
  const win = rangeWindow({ kind: "preset", id: "7d" }, NOW); // Sep 18 – Sep 25

  it("keeps a version published inside the window and drops one before it", () => {
    expect(publishedInWindow({ published_at: "2026-09-22T04:59:31Z" }, win)).toBe(true);
    expect(publishedInWindow({ published_at: "2026-09-10T07:48:00Z" }, win)).toBe(false);
  });

  it("is false with no date, a bad date or a broken window", () => {
    expect(publishedInWindow({}, win)).toBe(false);
    expect(publishedInWindow({ published_at: "yesterday" }, win)).toBe(false);
    expect(publishedInWindow({ published_at: "2026-09-22T04:59:31Z" }, { error: "x" })).toBe(false);
    expect(publishedInWindow(null, win)).toBe(false);
  });
});
