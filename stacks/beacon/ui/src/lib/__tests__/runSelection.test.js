// UIFPE-604: clicking or dragging on the Run history chart filters Recent
// runs. The one promise these tests hold: the rows a bar selects are exactly
// the runs that bar counts, because both use runHealth.runDay.
import { describe, expect, it } from "vitest";
import { dayBuckets, runDay } from "../runHealth.js";
import {
  RUN_FETCH_LIMIT,
  clickDay,
  finishDrag,
  historyCapped,
  inSelection,
  runsInSelection,
  selectDays,
  selectionLabel,
  selectionSummary,
} from "../runSelection.js";

const NOW = Date.UTC(2026, 8, 25, 12, 0, 0); // 2026-09-25T12:00:00Z
const at = (iso, status = "succeeded", extra = {}) => ({
  status,
  batch_id: `b-${iso}-${status}`,
  recorded_at: iso,
  ...extra,
});

const RUNS = [
  at("2026-09-25T03:00:00Z"),
  at("2026-09-15T23:59:59Z", "failed"),
  at("2026-09-15T10:00:00Z"),
  at("2026-09-15T00:00:00Z", "stopped"),
  at("2026-09-14T23:59:59Z"),
  at("2026-09-13T08:00:00Z", "failed"),
];

describe("runDay", () => {
  it("dates a run by its UTC day", () => {
    expect(runDay(at("2026-09-15T23:59:59Z"))).toBe("2026-09-15");
    expect(runDay(at("2026-09-16T00:00:00Z"))).toBe("2026-09-16");
  });

  it("falls back to started_at for a run still executing", () => {
    expect(runDay({ status: "running", started_at: "2026-09-25T09:00:00Z" })).toBe("2026-09-25");
  });

  it("is null for a run with no usable time", () => {
    expect(runDay({ status: "failed", recorded_at: "nope" })).toBeNull();
    expect(runDay(null)).toBeNull();
  });
});

describe("clickDay", () => {
  it("selects the clicked day", () => {
    expect(clickDay(null, "2026-09-15")).toEqual({ from: "2026-09-15", to: "2026-09-15" });
  });

  it("clicking the selected day again clears it", () => {
    expect(clickDay({ from: "2026-09-15", to: "2026-09-15" }, "2026-09-15")).toBeNull();
  });

  it("a click inside a range narrows to that day", () => {
    expect(clickDay({ from: "2026-09-13", to: "2026-09-17" }, "2026-09-15"))
      .toEqual({ from: "2026-09-15", to: "2026-09-15" });
  });

  it("a click off the bars keeps what was there", () => {
    const current = { from: "2026-09-15", to: "2026-09-15" };
    expect(clickDay(current, null)).toBe(current);
  });
});

describe("selectDays / finishDrag", () => {
  it("orders the range whichever way the drag went", () => {
    expect(selectDays("2026-09-17", "2026-09-13")).toEqual({ from: "2026-09-13", to: "2026-09-17" });
    expect(finishDrag(null, "2026-09-13", "2026-09-17")).toEqual({ from: "2026-09-13", to: "2026-09-17" });
    expect(finishDrag(null, "2026-09-17", "2026-09-13")).toEqual({ from: "2026-09-13", to: "2026-09-17" });
  });

  it("a press and release on the same bar is a click", () => {
    expect(finishDrag(null, "2026-09-15", "2026-09-15")).toEqual({ from: "2026-09-15", to: "2026-09-15" });
    expect(finishDrag({ from: "2026-09-15", to: "2026-09-15" }, "2026-09-15", "2026-09-15")).toBeNull();
  });

  it("a drag that never started keeps the selection", () => {
    const current = { from: "2026-09-15", to: "2026-09-15" };
    expect(finishDrag(current, null, "2026-09-17")).toBe(current);
  });

  it("orders across a month boundary", () => {
    expect(selectDays("2026-10-02", "2026-09-29")).toEqual({ from: "2026-09-29", to: "2026-10-02" });
  });
});

describe("runsInSelection", () => {
  it("keeps every run of the picked day, the midnight edges included", () => {
    const picked = runsInSelection(RUNS, { from: "2026-09-15", to: "2026-09-15" });
    expect(picked.map((r) => r.recorded_at)).toEqual([
      "2026-09-15T23:59:59Z",
      "2026-09-15T10:00:00Z",
      "2026-09-15T00:00:00Z",
    ]);
  });

  it("keeps both ends of a range", () => {
    const picked = runsInSelection(RUNS, { from: "2026-09-13", to: "2026-09-15" });
    expect(picked).toHaveLength(5);
    expect(picked.every((r) => inSelection(runDay(r), { from: "2026-09-13", to: "2026-09-15" }))).toBe(true);
  });

  it("no selection = every run, as before", () => {
    expect(runsInSelection(RUNS, null)).toBe(RUNS);
    expect(runsInSelection(undefined, null)).toEqual([]);
  });

  it("the rows a bar selects are exactly what that bar counts, plus unplotted runs", () => {
    for (const bar of dayBuckets(RUNS, 30, NOW)) {
      const picked = runsInSelection(RUNS, { from: bar.date, to: bar.date });
      expect(selectionSummary(picked).plotted).toBe(bar.SUCCEEDED + bar.FAILED);
    }
  });
});

describe("selectionSummary", () => {
  it("counts what is listed and says how many the chart does not draw", () => {
    const picked = runsInSelection(RUNS, { from: "2026-09-15", to: "2026-09-15" });
    expect(selectionSummary(picked)).toEqual({ total: 3, plotted: 2, unplotted: 1, text: "3 runs" });
    expect(selectionSummary([RUNS[0]]).text).toBe("1 run");
  });
});

describe("selectionLabel", () => {
  it("uses the chart's own day labels", () => {
    expect(selectionLabel({ from: "2026-09-15", to: "2026-09-15" })).toBe("09-15");
    expect(selectionLabel({ from: "2026-09-13", to: "2026-09-17" })).toBe("09-13 – 09-17");
    expect(selectionLabel(null)).toBe("");
  });
});

describe("historyCapped", () => {
  const recorded = (n) => Array.from({ length: n }, (_, i) => at(`2026-09-${String((i % 28) + 1).padStart(2, "0")}T00:00:00Z`));

  it("is true when the API returned the full cap", () => {
    expect(historyCapped(recorded(RUN_FETCH_LIMIT))).toBe(true);
    expect(historyCapped(recorded(RUN_FETCH_LIMIT - 1))).toBe(false);
  });

  it("does not count in-flight rows merged in on top of the cap", () => {
    const runs = [{ status: "running", started_at: "2026-09-25T09:00:00Z" }, ...recorded(RUN_FETCH_LIMIT - 1)];
    expect(historyCapped(runs)).toBe(false);
  });
});
