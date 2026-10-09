// insights.js (UIFPE-697)
// The Insights tabs — Overview, Jobs, Scheduled jobs — as pure functions over
// what the panel already loads: the run history (newest first), the catalog,
// the schedules and the destinations. Nothing here calls the API; every
// figure is counted from runs the page holds (the newest RUN_FETCH_LIMIT —
// UIFPE-703 replaces the counting with server aggregates).
//
// Owner decision 2026-09-29: a WARNING is a run that succeeded but rejected
// rows; stopped / skipped runs are neutral, never scored.
import { nextRun, pauseState } from "../schedule.js";

export const HOUR = 36e5;
export const DAY = 864e5;

/** succeeded | warning | failed | running | skipped | stopped | other. */
export function outcome(run) {
  const s = String(run?.status || "").toLowerCase();
  if (s === "succeeded") return run?.summary?.rejected_rows > 0 ? "warning" : "succeeded";
  return s || "unknown";
}

const at = (r) => Date.parse(r?.recorded_at || "");

/** "gold_product.product" → "product" — a source path as the table name people know. */
export function tableOf(path) {
  const parts = String(path || "").split(".").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

/** Runs recorded in (from, to] — both epoch ms. */
export function runsBetween(runs, from, to) {
  return (runs || []).filter((r) => {
    const t = at(r);
    return t > from && t <= to;
  });
}

/** Counts by outcome. */
export function countOutcomes(runs) {
  const c = { succeeded: 0, warning: 0, failed: 0, running: 0, skipped: 0, stopped: 0 };
  for (const r of runs || []) {
    const o = outcome(r);
    if (o in c) c[o] += 1;
  }
  return c;
}

/** "↑ 8% vs previous" / "↓ 2 vs previous" — the change from the window before. */
export function changeText(current, previous, mode = "pct") {
  if (mode === "abs") {
    const d = current - previous;
    return `${d >= 0 ? "↑" : "↓"} ${Math.abs(d)} vs previous`;
  }
  if (!previous) return current ? "↑ new" : "↑ 0% vs previous";
  const p = Math.round(((current - previous) / previous) * 100);
  return `${p >= 0 ? "↑" : "↓"} ${Math.abs(p)}% vs previous`;
}

/** Share of finished runs that delivered (warnings count as delivered). null = none finished. */
export function successPct(runs) {
  const c = countOutcomes(runs);
  const finished = c.succeeded + c.warning + c.failed;
  return finished ? Math.round(((c.succeeded + c.warning) / finished) * 100) : null;
}

/** n equal buckets from `from` (ms), each `span` ms, holding the runs in (start, end]. */
export function buckets(runs, from, n, span) {
  const out = Array.from({ length: n }, (_, i) => ({ from: from + i * span, to: from + (i + 1) * span, runs: [] }));
  for (const r of runs || []) {
    const t = at(r);
    const i = Math.ceil((t - from) / span) - 1;
    if (i >= 0 && i < n) out[i].runs.push(r);
  }
  return out;
}

/** UIFPE-713: a bucket's label on the Activity axis — "14:00" for the
 *  24-hour window, "Mon 29" for 7 days, "29 Sep" for 30 days (viewer's time). */
const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function bucketLabel(from, window) {
  const d = new Date(from);
  if (window === "24h") return `${String(d.getHours()).padStart(2, "0")}:00`;
  if (window === "7d") return `${WEEKDAY[d.getDay()]} ${d.getDate()}`;
  return `${d.getDate()} ${MONTH[d.getMonth()]}`;
}

/** Up to five evenly spaced bucket indexes for the axis, first and last included. */
export function axisTicks(n) {
  if (n <= 0) return [];
  return [...new Set([0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (n - 1))))];
}

/** The bucket holding the most rows: { index, rows }; index -1 when none has any. */
export function peakBucket(bucketList) {
  let best = { index: -1, rows: 0 };
  (bucketList || []).forEach((b, i) => {
    const rows = (b.runs || []).reduce((a, r) => a + (r.summary?.output_rows || 0), 0);
    if (rows > best.rows) best = { index: i, rows };
  });
  return best;
}

/** One row per export: its runs in the window (newest first) and the newest of them. */
export function perExport(catalog, runs) {
  return (catalog || []).map((c) => {
    const mine = (runs || []).filter((r) => r.job_id === c.job_id);
    return { entry: c, runs: mine, last: mine[0] || null };
  });
}

/** The last five runs, oldest first — the Jobs tab's dots read left to right. */
export function lastFive(runsNewestFirst) {
  return (runsNewestFirst || []).slice(0, 5).reverse();
}

/** Rank keys by finished runs, stacked by outcome, most first. `keysOf(run)` → keys. */
export function rankBy(runs, keysOf, limit = 4) {
  const m = new Map();
  for (const r of runs || []) {
    const o = outcome(r);
    if (o !== "succeeded" && o !== "warning" && o !== "failed") continue;
    for (const k of keysOf(r) || []) {
      if (!k) continue;
      const v = m.get(k) || { key: k, succeeded: 0, warning: 0, failed: 0, n: 0 };
      v[o] += 1;
      v.n += 1;
      m.set(k, v);
    }
  }
  return [...m.values()].sort((a, b) => b.n - a.n || String(a.key).localeCompare(String(b.key))).slice(0, limit);
}

/** Files delivered per destination name, from each run's recorded targets. */
export function filesByDestination(runs, nameOf) {
  const out = {};
  for (const r of runs || []) {
    for (const t of r?.summary?.targets || []) {
      if (t.status !== "delivered") continue;
      const name = nameOf(t);
      out[name] = (out[name] || 0) + (Number(t.delivered) || 0);
    }
  }
  return out;
}

/** Rows processed per weekday (0 = Sunday) × hour, in the viewer's time,
 *  averaged over `weeks`. */
export function heatGrid(runs, weeks = 1) {
  const g = Array.from({ length: 7 }, () => Array(24).fill(0));
  for (const r of runs || []) {
    const rows = r?.summary?.output_rows;
    if (!rows) continue;
    const d = new Date(at(r));
    g[d.getDay()][d.getHours()] += rows / weeks;
  }
  return g;
}

/** The calendar day of `t` in `timeZone`, as YYYY-MM-DD. */
function dayIn(t, timeZone) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: timeZone || "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(t));
  } catch {
    return new Date(t).toISOString().slice(0, 10);
  }
}

/** Every time a schedule will start its export in (from, to] — skipping a
 *  schedule that is off, and the days inside its pause window. */
export function scheduleOccurrences(schedules, from, to, perSchedule = Infinity) {
  const out = [];
  for (const s of schedules || []) {
    if (!s?.expression || s.enabled === false) continue;
    const paused = pauseState(s);
    let cursor = new Date(from);
    let found = 0;
    for (let i = 0; i < 400 && found < perSchedule; i += 1) {
      const next = nextRun(s.expression, s.timezone, cursor);
      if (!next || next.getTime() > to) break;
      const day = dayIn(next.getTime(), s.timezone);
      const inPause = paused && s.paused_until && day <= s.paused_until && (!s.paused_from || day >= s.paused_from);
      if (!inPause) {
        out.push({ schedule: s, job_id: s.job_id, at: next.getTime() });
        found += 1;
      }
      cursor = new Date(next.getTime() + 1000);
    }
  }
  return out.sort((a, b) => a.at - b.at);
}

/** The next time a schedule will really start its export — after any pause
 *  window — within `days`; null when off or nothing is due. */
export function nextFire(schedule, from = Date.now(), days = 62) {
  const [first] = scheduleOccurrences(schedule ? [schedule] : [], from, from + days * DAY, 1);
  return first ? new Date(first.at) : null;
}

// UIFPE-722: the caption under the Jobs tab's range — "Mon 15 Sep – Mon 29
// Sep · 42 runs across 6 exports" (days in the viewer's time)
// the short names bucketLabel uses (ICU spells September "Sept" in some locales)
export function dayLabel(ms) {
  const d = new Date(ms);
  return `${WEEKDAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]}`;
}

export function rangeCaption(from, to, runs) {
  const list = runs || [];
  const exports = new Set(list.map((r) => r.job_id).filter(Boolean)).size;
  return `${dayLabel(from)} – ${dayLabel(to)} · ${list.length} run${list.length === 1 ? "" : "s"} across ${exports} export${exports === 1 ? "" : "s"}`;
}
