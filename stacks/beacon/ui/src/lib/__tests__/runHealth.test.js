// UIFPE-579: a batch still executing is not a failure. These rules used to be
// inline in ExportDetail.jsx, where nothing tested them — and where "anything
// that is not succeeded" counted as a failure, so UIFPE-561's in-flight rows
// turned a healthy export red the moment someone pressed Run now.
import { describe, expect, it } from "vitest";
import { dayBuckets, fillDays, health, isInFlight, isScored, runDurationMs, runTargets, successRate } from "../runHealth.js";

const DAY = 864e5;
const NOW = Date.UTC(2026, 8, 24, 18, 0, 0); // 2026-09-24T18:00:00Z

const run = (status, agoDays = 0) => ({
  status,
  batch_id: `b-${status}-${agoDays}`,
  recorded_at: new Date(NOW - agoDays * DAY).toISOString(),
});

describe("health", () => {
  it("a running batch does not flip a healthy export to failing", () => {
    const runs = [run("running"), run("succeeded", 1), run("succeeded", 2),
                  run("succeeded", 3)];
    expect(health(runs)).toMatchObject({ state: "healthy", label: "Healthy" });
  });

  it("reads the last FINISHED run, not the row on top", () => {
    expect(health([run("running"), run("failed", 1), run("succeeded", 2)]))
      .toMatchObject({ state: "failing" });
  });

  it("still fails on a failed last run", () => {
    expect(health([run("failed"), run("succeeded", 1)]))
      .toMatchObject({ state: "failing" });
  });

  it("still fails on three consecutive failures", () => {
    expect(health([run("failed"), run("failed", 1), run("failed", 2),
                   run("succeeded", 3)])).toMatchObject({ state: "failing" });
  });

  it("degrades on two failures in the last ten", () => {
    const runs = [run("succeeded"), run("failed", 1), run("succeeded", 2),
                  run("failed", 3), run("succeeded", 4)];
    expect(health(runs)).toMatchObject({ state: "degraded", label: "Degraded" });
  });

  it("neutral rows say nothing about health", () => {
    expect(health([run("stopped"), run("skipped", 1), run("succeeded", 2)]))
      .toMatchObject({ state: "healthy" });
  });

  it("only in-flight and neutral rows means no outcome yet", () => {
    expect(health([run("running"), run("stopped", 1)]))
      .toMatchObject({ state: "unknown", label: "No runs yet" });
    expect(health([])).toMatchObject({ state: "unknown" });
  });

  it("an unreadable status is still treated as a failure, never hidden", () => {
    // record_run writes "unknown" when the event carries no status — a run
    // that may well have gone wrong, so it must not be quietly ignored
    expect(health([run("unknown"), run("succeeded", 1)]))
      .toMatchObject({ state: "failing" });
  });

  it("does not care about case", () => {
    expect(health([run("RUNNING"), run("SUCCEEDED", 1)]))
      .toMatchObject({ state: "healthy" });
  });
});

describe("successRate", () => {
  it("leaves a running batch out of the denominator", () => {
    expect(successRate([run("running"), run("succeeded", 1),
                        run("succeeded", 2), run("succeeded", 3)])).toBe(100);
  });

  it("counts failures among finished runs", () => {
    expect(successRate([run("succeeded"), run("failed", 1)])).toBe(50);
  });

  it("is null when nothing has finished", () => {
    expect(successRate([run("running")])).toBeNull();
    expect(successRate([])).toBeNull();
  });
});

describe("dayBuckets", () => {
  it("never plots a running batch as a failure", () => {
    const buckets = dayBuckets([run("running"), run("succeeded")], 30, NOW);
    expect(buckets).toEqual([
      { day: "09-24", date: "2026-09-24", SUCCEEDED: 1, FAILED: 0, WARNING: 0 },
    ]);
  });

  it("plots real outcomes per day, oldest last", () => {
    const buckets = dayBuckets([run("failed"), run("succeeded"),
                                run("succeeded", 1)], 30, NOW);
    expect(buckets.map((b) => b.day)).toEqual(["09-23", "09-24"]);
    expect(buckets[1]).toMatchObject({ SUCCEEDED: 1, FAILED: 1 });
  });

  it("drops runs older than the range and unparseable timestamps", () => {
    const runs = [run("succeeded", 40), { status: "failed", recorded_at: "nope" }];
    expect(dayBuckets(runs, 30, NOW)).toEqual([]);
  });

  it("skips neutral rows", () => {
    expect(dayBuckets([run("stopped"), run("missed")], 30, NOW)).toEqual([]);
  });
});

describe("row classification", () => {
  it("separates in-flight from scored", () => {
    expect(isInFlight(run("running"))).toBe(true);
    expect(isInFlight(run("starting"))).toBe(true);
    expect(isInFlight(run("succeeded"))).toBe(false);
    expect(isScored(run("running"))).toBe(false);
    expect(isScored(run("stopped"))).toBe(false);
    expect(isScored(run("failed"))).toBe(true);
    expect(isScored({})).toBe(false);
  });
});

describe("fillDays (UIFPE-721)", () => {
  it("draws every day of the range, empty ones as zeros, oldest first", () => {
    const filled = fillDays(dayBuckets([run("succeeded", 2)], 7, NOW), 7, NOW);
    expect(filled).toHaveLength(8); // the first, partial day through today
    expect(filled[0].date).toBe("2026-09-17");
    expect(filled.at(-1).date).toBe("2026-09-24");
    expect(filled.find((b) => b.date === "2026-09-22")).toMatchObject({ SUCCEEDED: 1, FAILED: 0 });
    expect(filled.find((b) => b.date === "2026-09-23")).toMatchObject({ SUCCEEDED: 0, FAILED: 0, day: "09-23" });
  });
  it("keeps a range with no runs as a row of empty days", () => {
    expect(fillDays([], 1, NOW).map((b) => b.date)).toEqual(["2026-09-23", "2026-09-24"]);
  });
});

describe("runDurationMs (UIFPE-721)", () => {
  it("is the end minus the start", () => {
    expect(runDurationMs({ started_at: "2026-09-24T18:00:00Z", recorded_at: "2026-09-24T18:04:30Z" })).toBe(270000);
  });
  it("is null without a start, or when the times cannot be read", () => {
    expect(runDurationMs({ recorded_at: "2026-09-24T18:04:30Z" })).toBeNull();
    expect(runDurationMs({ started_at: "nope", recorded_at: "2026-09-24T18:04:30Z" })).toBeNull();
    expect(runDurationMs({ started_at: "2026-09-24T18:05:00Z", recorded_at: "2026-09-24T18:04:30Z" })).toBeNull();
  });
});

describe("runTargets (UIFPE-721)", () => {
  const dests = [{ id: "d1", name: "Ingram SFTP" }];
  const label = (c) => ({ s3: "AWS S3", sftp: "SFTP", email: "Email" })[c] || c;
  it("reads a finished run's per-target outcomes", () => {
    const r = { status: "succeeded", summary: { targets: [
      { target: 0, channel: "sftp", destination_id: "d1", status: "delivered", delivered: 2 },
      { target: 1, channel: "email", destination_id: "gone", status: "delivered", delivered: 1 },
    ] } };
    expect(runTargets(r, dests, label)).toEqual([
      { label: "Ingram SFTP", ok: true, status: "delivered", error: null },
      { label: "(removed destination)", ok: true, status: "delivered", error: null },
    ]);
  });
  it("names the managed drop when it is the only target", () => {
    const r = { summary: { targets: [{ target: 0, channel: "s3", status: "delivered" }] } };
    expect(runTargets(r, dests, label)[0].label).toBe("Managed S3 partner-drop");
  });
  it("reads a failed delivery's targets out of its error, best-effort", () => {
    const error = JSON.stringify({ Error: "RuntimeError", Cause: JSON.stringify({ errorMessage:
      'delivery failed for 1 of 1 target(s): [{"target": 0, "channel": "sftp", "destination_id": "d1", "status": "failed", "error": "timeout"}]' }) });
    expect(runTargets({ status: "failed", error }, dests, label)).toEqual([
      { label: "Ingram SFTP", ok: false, status: "failed", error: "timeout" },
    ]);
  });
  it("is null when the run says nothing about its targets", () => {
    expect(runTargets({ status: "failed", error: "quality gate breached" }, dests, label)).toBeNull();
    expect(runTargets({ status: "succeeded", summary: {} }, dests, label)).toBeNull();
  });
});
