// overviewRange.js (UIFPE-610)
// The Overview's time range — CloudWatch-style quick picks plus a custom
// from/to — turned into the [since, until) window GET /runs/summary counts,
// and the summary's raw counts turned into what the donut and KPIs show.
//
// Custom dates are CALENDAR days in the viewer's own timezone: "Sep 1 – Sep 20"
// means from local midnight on Sep 1 up to (not including) local midnight on
// Sep 21, sent to the API as UTC instants. Quick picks are rolling windows
// ending now, like CloudWatch's.
import { isInFlight, isScored } from "./runHealth.js";

export const PRESETS = [
  { id: "24h", days: 1, label: "24 hours" },
  { id: "7d", days: 7, label: "7 days" },
  { id: "30d", days: 30, label: "30 days" },
  { id: "90d", days: 90, label: "90 days" },
];
export const DEFAULT_RANGE = { kind: "preset", id: "7d" };
export const MAX_CUSTOM_DAYS = 366; // the API's own limit

const DAY = 864e5;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "YYYY-MM-DD" of a Date in the viewer's timezone — the value a date input holds. */
export function localDay(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Local midnight at the start of a "YYYY-MM-DD" day, or null. */
function startOfLocalDay(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) || localDay(d) !== day ? null : d;
}

function shortDate(day) {
  const d = startOfLocalDay(day);
  return d ? `${MONTHS[d.getMonth()]} ${d.getDate()}` : day;
}

/** The window a range covers: { since, until, label } (ISO strings), or
 *  { error } for a custom range that cannot be asked for. */
export function rangeWindow(range, now = new Date()) {
  if (!range || range.kind !== "custom") {
    const preset = PRESETS.find((p) => p.id === range?.id) || PRESETS[1];
    return {
      since: new Date(now.getTime() - preset.days * DAY).toISOString(),
      until: now.toISOString(),
      label: preset.label,
    };
  }
  const from = startOfLocalDay(range.from);
  const to = startOfLocalDay(range.to);
  if (!from || !to) return { error: "Pick both a start and an end date." };
  if (from > to) return { error: "The start date must be on or before the end date." };
  if (from > now) return { error: "The start date is in the future — there are no runs yet." };
  // the day AFTER `to`, at local midnight — counts the whole end day
  const until = new Date(to.getFullYear(), to.getMonth(), to.getDate() + 1);
  if ((until - from) / DAY > MAX_CUSTOM_DAYS + 1)
    return { error: `Pick at most ${MAX_CUSTOM_DAYS} days.` };
  const label = range.from === range.to ? shortDate(range.from) : `${shortDate(range.from)} – ${shortDate(range.to)}`;
  return { since: from.toISOString(), until: until.toISOString(), label };
}

/** The summary's by_status counts, classified by the same rules as the
 *  export page (runHealth): succeeded; failed = every other outcome,
 *  including an unreadable status; neutral = stopped / skipped / missed;
 *  in-flight rows (none are recorded today) are left out. */
export function outcomeCounts(byStatus) {
  const counts = { succeeded: 0, failed: 0, neutral: 0, total: 0 };
  for (const [status, n] of Object.entries(byStatus || {})) {
    const count = Number(n) || 0;
    const run = { status };
    counts.total += count;
    if (isInFlight(run)) continue;
    if (!isScored(run)) counts.neutral += count;
    else if (status.toLowerCase() === "succeeded") counts.succeeded += count;
    else counts.failed += count;
  }
  return counts;
}

/** Success rate among finished outcomes, or null when there are none. */
export function successRateOf(counts) {
  const scored = counts.succeeded + counts.failed;
  return scored ? Math.round((counts.succeeded / scored) * 100) : null;
}

// ---- the range in a URL (UIFPE-754) ----------------------------------------
// Home's KPI tiles link to the Exports list and to Insights → Jobs carrying
// the chosen window: `?range=7d` for a quick pick, `?from=YYYY-MM-DD&to=…`
// for a custom one. Both pages read it back on load.

/** The query parameters that carry a range: { range } or { from, to }. */
export function rangeToParams(range) {
  if (range?.kind === "custom") return { from: range.from, to: range.to };
  const preset = PRESETS.find((p) => p.id === range?.id) || PRESETS[1];
  return { range: preset.id };
}

/** The range a URL carries, or null when it carries none (or nonsense).
 *  `params` is anything with a `get(name)`, like URLSearchParams. */
export function rangeFromParams(params) {
  const id = params?.get?.("range");
  if (id && PRESETS.some((p) => p.id === id)) return { kind: "preset", id };
  const from = params?.get?.("from");
  const to = params?.get?.("to");
  if (from && to && startOfLocalDay(from) && startOfLocalDay(to) && from <= to) return { kind: "custom", from, to };
  return null;
}

/** Whether a catalog entry's current version was published inside the window
 *  ([since, until) ISO strings, as rangeWindow gives). No date = outside. */
export function publishedInWindow(entry, window) {
  const at = Date.parse(entry?.published_at);
  if (Number.isNaN(at) || !window || window.error) return false;
  return at >= Date.parse(window.since) && at < Date.parse(window.until);
}
