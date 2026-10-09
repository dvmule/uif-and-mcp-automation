import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  DAY,
  HOUR,
  axisTicks,
  bucketLabel,
  buckets,
  changeText,
  countOutcomes,
  filesByDestination,
  heatGrid,
  lastFive,
  nextFire,
  outcome,
  peakBucket,
  perExport,
  rankBy,
  runsBetween,
  scheduleOccurrences,
  successPct,
  tableOf,
  dayLabel,
  rangeCaption,
} from "../insights.js";

const NOW = Date.parse("2026-09-29T09:12:00Z");
const run = (job, status, hoursAgo, extra = {}) => ({
  job_id: job,
  status,
  recorded_at: new Date(NOW - hoursAgo * HOUR).toISOString(),
  ...extra,
});

describe("outcome — the warning rule", () => {
  it("is a warning when a succeeded run rejected rows", () => {
    expect(outcome(run("a", "succeeded", 1, { summary: { rejected_rows: 3 } }))).toBe("warning");
  });
  it("stays succeeded with no rejects, and passes other statuses through", () => {
    expect(outcome(run("a", "succeeded", 1, { summary: { rejected_rows: 0 } }))).toBe("succeeded");
    expect(outcome(run("a", "FAILED", 1))).toBe("failed");
    expect(outcome(run("a", "skipped", 1))).toBe("skipped");
  });
});

describe("windows and counts", () => {
  const runs = [
    run("a", "succeeded", 1),
    run("a", "failed", 5),
    run("b", "succeeded", 30, { summary: { rejected_rows: 2 } }),
    run("b", "stopped", 40),
  ];
  it("keeps the runs inside (from, to]", () => {
    expect(runsBetween(runs, NOW - DAY, NOW)).toHaveLength(2);
    expect(runsBetween(runs, NOW - 2 * DAY, NOW - DAY)).toHaveLength(2);
  });
  it("counts by outcome", () => {
    expect(countOutcomes(runs)).toMatchObject({ succeeded: 1, failed: 1, warning: 1, stopped: 1 });
  });
  it("scores warnings as delivered and leaves stopped out", () => {
    expect(successPct(runs)).toBe(67);
    expect(successPct([run("a", "stopped", 1)])).toBeNull();
  });
  it("says the change against the window before", () => {
    expect(changeText(12, 11)).toBe("↑ 9% vs previous");
    expect(changeText(0, 2, "abs")).toBe("↓ 2 vs previous");
    expect(changeText(1, 0)).toBe("↑ new");
    expect(changeText(0, 0)).toBe("↑ 0% vs previous");
  });
});

describe("buckets and per-export rows", () => {
  it("drops each run into its hour", () => {
    const bs = buckets([run("a", "succeeded", 0.5), run("a", "succeeded", 23.5)], NOW - DAY, 24, HOUR);
    expect(bs[23].runs).toHaveLength(1);
    expect(bs[0].runs).toHaveLength(1);
  });
  it("gives every export its runs, newest first, and the last five oldest first", () => {
    const runs = [run("a", "succeeded", 1), run("a", "failed", 2), run("b", "succeeded", 3)];
    const rows = perExport([{ job_id: "a" }, { job_id: "c" }], runs);
    expect(rows.map((r) => r.runs.length)).toEqual([2, 0]);
    expect(rows[0].last.status).toBe("succeeded");
    expect(lastFive(rows[0].runs).map((r) => r.status)).toEqual(["failed", "succeeded"]);
  });
});

describe("Activity axis and peak (UIFPE-713)", () => {
  it("labels a bucket by hour, weekday or date", () => {
    const t = new Date(2026, 8, 29, 14, 0).getTime();
    expect(bucketLabel(t, "24h")).toBe("14:00");
    expect(bucketLabel(t, "7d")).toBe("Tue 29");
    expect(bucketLabel(t, "30d")).toBe("29 Sep");
  });
  it("spreads at most five ticks, first and last included", () => {
    expect(axisTicks(24)).toEqual([0, 6, 12, 17, 23]);
    expect(axisTicks(3)).toEqual([0, 1, 2]);
    expect(axisTicks(0)).toEqual([]);
  });
  it("finds the bucket with the most rows", () => {
    const bs = [{ runs: [] }, { runs: [{ summary: { output_rows: 5 } }, { summary: { output_rows: 7 } }] }, { runs: [{ summary: { output_rows: 9 } }] }];
    expect(peakBucket(bs)).toEqual({ index: 1, rows: 12 });
    expect(peakBucket([{ runs: [] }])).toEqual({ index: -1, rows: 0 });
  });
});

describe("rankings", () => {
  it("ranks keys by finished runs, stacked by outcome", () => {
    const runs = [
      run("a", "succeeded", 1),
      run("a", "failed", 2),
      run("b", "succeeded", 3),
      run("b", "running", 0),
    ];
    const keys = { a: ["product", "price"], b: ["product"] };
    const ranked = rankBy(runs, (r) => keys[r.job_id]);
    expect(ranked[0]).toMatchObject({ key: "product", n: 3, succeeded: 2, failed: 1 });
    expect(ranked[1]).toMatchObject({ key: "price", n: 2 });
  });
  it("names a source by its table", () => {
    expect(tableOf("gold_product.product")).toBe("product");
    expect(tableOf("")).toBe("");
  });
  it("adds up delivered files per destination", () => {
    const runs = [
      run("a", "succeeded", 1, { summary: { targets: [{ destination_id: "d1", status: "delivered", delivered: 2 }] } }),
      run("a", "failed", 2, { summary: { targets: [{ destination_id: "d1", status: "failed" }] } }),
    ];
    expect(filesByDestination(runs, (t) => t.destination_id)).toEqual({ d1: 2 });
  });
  it("sums rows per weekday and hour", () => {
    const g = heatGrid([run("a", "succeeded", 0, { summary: { output_rows: 100 } })]);
    const d = new Date(NOW);
    expect(g[d.getDay()][d.getHours()]).toBe(100);
  });
});

describe("scheduleOccurrences", () => {
  const from = Date.parse("2026-09-29T00:00:00Z");
  // UIFPE-751: pauseState() reads today's date from the real clock, so the
  // pause-window cases below only held while 29–30 Sep 2026 was "today".
  // Pin the clock to NOW for this block; Date.* alone is mocked, timers are not.
  beforeAll(() => vi.setSystemTime(NOW));
  afterAll(() => vi.useRealTimers());
  it("lists every daily fire in the window", () => {
    const occ = scheduleOccurrences([{ job_id: "a", expression: "cron(0 6 * * ? *)", timezone: "UTC", enabled: true }], from, from + 3 * DAY);
    expect(occ.map((o) => new Date(o.at).toISOString())).toEqual([
      "2026-09-29T06:00:00.000Z",
      "2026-09-30T06:00:00.000Z",
      "2026-10-01T06:00:00.000Z",
    ]);
  });
  it("skips a schedule that is off and the days inside a pause window", () => {
    const off = { job_id: "b", expression: "cron(0 6 * * ? *)", timezone: "UTC", enabled: false };
    const paused = { job_id: "c", expression: "cron(0 6 * * ? *)", timezone: "UTC", enabled: true, paused_from: "2026-09-29", paused_until: "2026-09-30" };
    const occ = scheduleOccurrences([off, paused], from, from + 3 * DAY);
    expect(occ.map((o) => new Date(o.at).toISOString())).toEqual(["2026-10-01T06:00:00.000Z"]);
  });
  it("gives the next real fire after a pause window, and none for a schedule that is off", () => {
    const paused = { job_id: "c", expression: "cron(0 6 * * ? *)", timezone: "UTC", enabled: true, paused_from: "2026-09-29", paused_until: "2026-09-30" };
    expect(nextFire(paused, from).toISOString()).toBe("2026-10-01T06:00:00.000Z");
    expect(nextFire({ ...paused, enabled: false }, from)).toBeNull();
  });
});

describe("rangeCaption (UIFPE-722)", () => {
  const at = (iso) => new Date(iso).getTime();
  it("names the days, the runs and the exports they came from", () => {
    const runs = [{ job_id: "1" }, { job_id: "1" }, { job_id: "2" }];
    const text = rangeCaption(at("2026-09-15T12:00:00"), at("2026-09-29T12:00:00"), runs);
    expect(text).toBe("Tue 15 Sep – Tue 29 Sep · 3 runs across 2 exports");
  });
  it("says one run and one export in the singular", () => {
    expect(rangeCaption(at("2026-09-29T08:00:00"), at("2026-09-29T09:00:00"), [{ job_id: "7" }])).toBe(
      "Tue 29 Sep – Tue 29 Sep · 1 run across 1 export"
    );
    expect(dayLabel(at("2026-09-30T10:00:00"))).toBe("Wed 30 Sep");
  });
});
