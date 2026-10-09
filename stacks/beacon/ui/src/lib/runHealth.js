// runHealth.js (UIFPE-579)
// How a run list becomes the export detail screen's health pill, success rate
// and history chart. Extracted from ExportDetail.jsx to be testable: the
// rules were inline, and the one assumption they all shared — that every row
// is a FINISHED run — silently broke when UIFPE-561 started merging
// still-executing runs into GET /runs.
//
// Three kinds of row, and the difference matters:
//   outcome   succeeded / failed — the only rows that say whether it works
//   neutral   stopped (UIFPE-203), skipped / missed (UIFPE-206) — an operator
//             or the scheduler decided; says nothing about the export
//   in flight running / starting — no outcome YET, so it cannot be either
//
// Anything else (e.g. record_run's "unknown" fallback) is deliberately still
// scored as a failure: a run whose status could not be read is a run that may
// have gone wrong, and quietly ignoring it would hide real breakage.

const NEUTRAL = new Set(["stopped", "skipped", "missed"]);
const IN_FLIGHT = new Set(["running", "starting"]);

const statusOf = (run) => String(run?.status || "").toLowerCase();

/** Rows that carry an outcome — the only ones health and the rate may read. */
export function isScored(run) {
  const status = statusOf(run);
  return Boolean(status) && !NEUTRAL.has(status) && !IN_FLIGHT.has(status);
}

export function isInFlight(run) {
  return IN_FLIGHT.has(statusOf(run));
}

export function scoredRuns(runs) {
  return (runs || []).filter(isScored);
}

/** Derived health, same rules the platform uses: failing when the last
 *  finished run failed or 3+ consecutive failures; degraded on 2+ failures in
 *  the last 10. A batch in flight never flips the pill — it has not finished. */
export function health(allRuns) {
  const runs = scoredRuns(allRuns);
  if (!runs.length) return { state: "unknown", label: "No runs yet", tone: "mute" };
  const recent = runs.slice(0, 10);
  const consecutive = runs.findIndex((r) => statusOf(r) === "succeeded");
  const fails = recent.filter((r) => statusOf(r) !== "succeeded").length;
  if (statusOf(runs[0]) !== "succeeded" || consecutive >= 3)
    return { state: "failing", label: "Failing", tone: "fail" };
  if (fails >= 2) return { state: "degraded", label: "Degraded", tone: "warn" };
  return { state: "healthy", label: "Healthy", tone: "ok" };
}

/** Percentage of finished runs that succeeded, or null when none have. */
export function successRate(allRuns) {
  const runs = scoredRuns(allRuns);
  if (!runs.length) return null;
  const ok = runs.filter((r) => statusOf(r) === "succeeded").length;
  return Math.round((ok / runs.length) * 100);
}

/** The calendar day (UTC, "YYYY-MM-DD") a run belongs to, or null. ONE rule
 *  shared by the chart's bars and the table filter behind them (UIFPE-604),
 *  so a bar's count and the rows it selects always agree. A finished run is
 *  dated by recorded_at; one still executing has only started_at. */
export function runDay(run) {
  const ts = new Date(run?.recorded_at || run?.started_at).getTime();
  return Number.isNaN(ts) ? null : new Date(ts).toISOString().slice(0, 10);
}

/** Outcomes per day for the history chart, oldest last. Only finished runs
 *  are plotted: a batch still executing has no outcome to draw, and drawing
 *  it as a failure is what UIFPE-579 fixed. It stays visible in Recent runs.
 *  Each bucket carries `day` (the "MM-DD" axis label) and `date` (the full
 *  day the chart selection filters on). */
export function dayBuckets(allRuns, rangeDays, nowMs = Date.now()) {
  const cutoff = nowMs - rangeDays * 864e5;
  const byDay = new Map();
  for (const run of scoredRuns(allRuns)) {
    const ts = new Date(run.recorded_at).getTime();
    if (Number.isNaN(ts) || ts < cutoff) continue;
    const date = runDay(run);
    const row = byDay.get(date) || { day: date.slice(5), date, SUCCEEDED: 0, FAILED: 0, WARNING: 0 };
    row[statusOf(run) === "succeeded" ? "SUCCEEDED" : "FAILED"] += 1;
    byDay.set(date, row);
  }
  return [...byDay.values()].reverse();
}

// UIFPE-721: the insight page draws EVERY day of the range, empty ones as
// zero bars, so a quiet week reads as quiet rather than as a short chart.
// Days are UTC calendar days, the same keys runDay() gives the buckets.
export function fillDays(buckets, rangeDays, nowMs = Date.now()) {
  const byDate = new Map((buckets || []).map((b) => [b.date, b]));
  const dayStart = (ms) => {
    const d = new Date(ms);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  };
  const first = dayStart(nowMs - rangeDays * 864e5);
  const last = dayStart(nowMs);
  const out = [];
  for (let t = first; t <= last; t += 864e5) {
    const date = new Date(t).toISOString().slice(0, 10);
    out.push(byDate.get(date) || { day: date.slice(5), date, SUCCEEDED: 0, FAILED: 0, WARNING: 0 });
  }
  return out;
}

// UIFPE-721: how long a finished run took, in milliseconds — recorded_at
// (its end) minus started_at (UIFPE-720). Runs recorded before started_at
// existed have no duration: null, never a guess.
export function runDurationMs(run) {
  const start = Date.parse(run?.started_at);
  const end = Date.parse(run?.recorded_at);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return end - start;
}

// UIFPE-721: where one run delivered, target by target. A finished run
// carries the deliver step's per-target outcomes in its summary; a run whose
// delivery failed names them inside its error ("… target(s): [{…}]"), which is
// read best-effort. Anything else: null (the page shows a dash).
export function runTargets(run, destinations = [], channelLabel = (c) => c) {
  let outcomes = Array.isArray(run?.summary?.targets) ? run.summary.targets : null;
  if (!outcomes && typeof run?.error === "string") {
    const at = run.error.indexOf("target(s): [");
    if (at >= 0) {
      const text = run.error.slice(at + "target(s): ".length).replace(/\\+"/g, '"').replace(/\\"/g, '"');
      const end = text.indexOf("}]");
      if (end > 0) {
        try {
          outcomes = JSON.parse(text.slice(0, end + 2));
        } catch {
          outcomes = null;
        }
      }
    }
  }
  if (!Array.isArray(outcomes) || !outcomes.length) return null;
  return outcomes.map((o) => {
    const dest = o.destination_id ? destinations.find((d) => d.id === o.destination_id) : null;
    const label = dest
      ? dest.name
      : o.destination_id
        ? "(removed destination)"
        : o.channel === "s3" && outcomes.length === 1
          ? "Managed S3 partner-drop"
          : channelLabel(o.channel);
    return { label, ok: o.status === "delivered", status: o.status || "unknown", error: o.error || null };
  });
}
