// The slot arithmetic exists TWICE: authoritatively in
// src/apps/scripts/gold_frequencies.py (what the dispatcher actually runs) and
// here, because the wizard has to preview a table's run times before anything is
// deployed. Two implementations of "when does this pipeline run" is a drift risk
// with a nasty failure mode — the UI promises 14:30, the dispatcher runs 10:30,
// and nothing fails.
//
// So both sides assert the SAME vector file. qe/test_gold_frequency_parity.py
// runs these exact cases against the Python module; this file runs them against
// the TypeScript one. Add a case there and it is enforced on both sides at once.
import { describe, it, expect } from "vitest";
import vector from "../../../../qe/gold/fixtures/schedule-slots.json";
import {
  GOLD_CADENCES, GOLD_WEEKLY, FREQUENCIES, slotMinutes, previousSlot, nextSlot,
  parseTimeOfDay, formatTimeOfDay, normalizeStartTime, runTimesPreview,
  defaultStartTimeFor, parseWeeklyDaysLine, formatWeekdays,
  type GoldFrequency,
} from "../pipelines";

const MINUTES_PER_DAY = 1440;

describe("shared slot vector", () => {
  it("is not empty (an emptied fixture would make both sides trivially pass)", () => {
    expect(vector.slots.length).toBeGreaterThanOrEqual(10);
    expect(vector.previousSlot.length).toBeGreaterThanOrEqual(8);
  });

  for (const c of vector.slots) {
    it(`slots — ${c.why}`, () => {
      const got = slotMinutes(c.start_time, c.frequency as GoldFrequency);
      if ("expectAll" in c) expect(got).toEqual(c.expectAll);
      if ("expectCount" in c) expect(got.length).toBe(c.expectCount);
      if ("expectFirst" in c) expect(got[0]).toBe(c.expectFirst);
      if ("expectLast" in c) expect(got[got.length - 1]).toBe(c.expectLast);
    });
  }

  for (const c of vector.previousSlot) {
    it(`previousSlot — ${c.why}`, () => {
      // weekly cases carry a day set (UIFPE-532); every other rung ignores it
      const days = (c as { weekly_days?: string[] }).weekly_days;
      const got = previousSlot(new Date(c.now), c.start_time,
                               c.frequency as GoldFrequency, days);
      expect(got?.toISOString()).toBe(new Date(c.expect).toISOString());
    });
  }
});

describe("ladder invariants", () => {
  it("every interval divides a day, so slots never gap at midnight", () => {
    for (const c of GOLD_CADENCES) {
      expect(MINUTES_PER_DAY % c.minutes).toBe(0);
    }
  });

  it("slots are uniformly spaced including across midnight", () => {
    for (const c of GOLD_CADENCES) {
      const slots = slotMinutes("06:30", c.value);
      const gaps = slots.slice(1).map((t, i) => t - slots[i]);
      gaps.push(slots[0] + MINUTES_PER_DAY - slots[slots.length - 1]);
      expect(new Set(gaps)).toEqual(new Set([c.minutes]));
    }
  });

  it("nextSlot is exactly one interval after previousSlot", () => {
    const now = new Date("2026-08-12T15:00:00Z");
    for (const c of GOLD_CADENCES) {
      const prev = previousSlot(now, "06:30", c.value)!;
      const next = nextSlot(now, "06:30", c.value)!;
      expect(next.getTime() - prev.getTime()).toBe(c.minutes * 60_000);
      expect(next.getTime()).toBeGreaterThan(now.getTime());
    }
  });
});

describe("time-of-day parsing", () => {
  it("accepts valid times", () => {
    expect(parseTimeOfDay("00:00")).toBe(0);
    expect(parseTimeOfDay("06:30")).toBe(390);
    expect(parseTimeOfDay("23:59")).toBe(1439);
    expect(parseTimeOfDay(" 06:30 ")).toBe(390);
  });

  it("rejects everything that is not a time of day", () => {
    // Every one of these is a value JavaScript's Number() would silently
    // coerce, which is exactly how this diverged from the Python twin.
    for (const bad of ["", "6:30pm", "24:00", "06:60", "-1:00", "0630",
                       "06:30:00", "06", ":30", "30:", "aa:bb", "06: 30",
                       "6e1:30", "+6:30", "0x6:30", "006:30", " : ",
                       undefined]) {
      expect(parseTimeOfDay(bad as string)).toBeNull();
    }
  });

  it("round-trips through formatTimeOfDay", () => {
    for (const m of [0, 1, 59, 390, 720, 1439]) {
      expect(parseTimeOfDay(formatTimeOfDay(m))).toBe(m);
    }
  });
});

describe("normalizeStartTime", () => {
  it("falls back to the cadence default when absent or invalid", () => {
    expect(normalizeStartTime(undefined, "gold-24h")).toBe("06:30");
    expect(normalizeStartTime("nonsense", "gold-1h")).toBe("00:45");
    expect(normalizeStartTime("25:00", "gold-5m")).toBe("00:00");
  });

  it("keeps a valid time", () => {
    expect(normalizeStartTime("14:45", "gold-4h")).toBe("14:45");
  });

  it("agrees with defaultStartTimeFor for every cadence", () => {
    for (const c of GOLD_CADENCES) {
      expect(normalizeStartTime(undefined, c.value))
        .toBe(defaultStartTimeFor(c.value));
    }
  });
});

describe("the wizard's cadence dropdown (UIFPE-532)", () => {
  it("offers the ladder in order and the weekly rung last", () => {
    expect(FREQUENCIES.map((f) => f.value))
      .toEqual([...GOLD_CADENCES.map((c) => c.value), "gold-weekly"]);
    expect(FREQUENCIES[FREQUENCIES.length - 1].label).toBe("weekly");
  });
});

describe("weekly_days in a pipeline YAML", () => {
  it("reads the compiler's flow list, normalised, and defaults to Monday", () => {
    expect(parseWeeklyDaysLine("schedule:\n  frequency: gold-weekly\n  start_time: \"03:00\"\n  weekly_days: [thu, TUE]\n"))
      .toEqual(["TUE", "THU"]);
    expect(parseWeeklyDaysLine("schedule:\n  frequency: gold-weekly\n  weekly_days: [\"SAT\", 'SUN']\n"))
      .toEqual(["SAT", "SUN"]);
    expect(parseWeeklyDaysLine("schedule:\n  frequency: gold-24h\n")).toEqual(["MON"]);
    expect(parseWeeklyDaysLine("schedule:\n  weekly_days: [FUNDAY]\n")).toEqual(["MON"]);
    expect(parseWeeklyDaysLine("schedule:\n  weekly_days: []\n")).toEqual(["MON"]);
  });

  it("names days as the schedule copy does", () => {
    expect(formatWeekdays(["TUE", "THU"])).toBe("Tue, Thu");
    expect(formatWeekdays(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"])).toBe("every day");
  });
});

describe("runTimesPreview", () => {
  it("lists every slot when they fit", () => {
    expect(runTimesPreview("06:30", "gold-8h"))
      .toEqual({ times: ["06:30", "14:30", "22:30"], elided: 0 });
  });

  it("elides the tail for dense cadences rather than printing 288 times", () => {
    const p = runTimesPreview("00:00", "gold-5m");
    expect(p.times).toEqual(["00:00", "00:05", "00:10", "00:15", "00:20",
                             "00:25"]);
    expect(p.elided).toBe(282);
  });

  it("previews the weekly rung as its single run time (the days are named beside it)", () => {
    expect(runTimesPreview("03:00", GOLD_WEEKLY.value)).toEqual({ times: ["03:00"], elided: 0 });
  });

  it("shows the phased first slot, not the start time, when they differ", () => {
    // 14:45 on a 4-hour cadence phases the grid to 02:45 — the preview has to
    // show that rather than implying the first run of the day is at 14:45.
    expect(runTimesPreview("14:45", "gold-4h").times[0]).toBe("02:45");
  });
});
