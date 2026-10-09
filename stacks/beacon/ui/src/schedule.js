// Schedule helpers (UIFPE-207) — pure functions, no API calls.
//
// The stored value is ALWAYS an AWS EventBridge Scheduler cron expression
// plus a separate IANA timezone (the backend decision on UIFPE-206/207): the
// picker here is just a friendly front door that writes the cron string. Only
// the three picker shapes are produced; anything else that appears in a
// record (a future "advanced" cron) still gets a readable fallback.
//
// AWS cron is 6 fields — minute hour day-of-month month day-of-week year —
// and day-of-month / day-of-week cannot both carry values: one must be "?".

export const WEEKDAYS = [
  ["MON", "Monday"],
  ["TUE", "Tuesday"],
  ["WED", "Wednesday"],
  ["THU", "Thursday"],
  ["FRI", "Friday"],
  ["SAT", "Saturday"],
  ["SUN", "Sunday"],
];

// Offered zones: the ones Pearson teams actually deliver feeds in. The
// browser's own zone is prepended by the component when it isn't listed.
export const TIMEZONES = [
  "Europe/London",
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Asia/Kolkata",
  "Australia/Sydney",
];

const pad2 = (n) => String(n).padStart(2, "0");

/** Picker state -> AWS cron expression. mode: daily | weekly | monthly. */
export function pickerToCron({ mode, time, weekday, monthday }) {
  const [hh, mm] = (time || "06:00").split(":").map(Number);
  if (mode === "weekly") return `cron(${mm} ${hh} ? * ${weekday || "MON"} *)`;
  if (mode === "monthly") return `cron(${mm} ${hh} ${monthday || 1} * ? *)`;
  return `cron(${mm} ${hh} * * ? *)`;
}

/** Cron expression -> picker state, or null when it isn't a picker shape
 *  (then the UI shows the raw cron read-only instead of a lying picker). */
export function cronToPicker(expression) {
  const m = /^cron\((\d{1,2}) (\d{1,2}) (\S+) \* (\S+) \*\)$/.exec(expression || "");
  if (!m) return null;
  const [, mm, hh, dom, dow] = m;
  const time = `${pad2(hh)}:${pad2(mm)}`;
  if (dom === "*" && dow === "?") return { mode: "daily", time };
  if (dom === "?" && WEEKDAYS.some(([id]) => id === dow))
    return { mode: "weekly", time, weekday: dow };
  if (dow === "?" && /^\d{1,2}$/.test(dom) && +dom >= 1 && +dom <= 31)
    return { mode: "monthly", time, monthday: +dom };
  return null;
}

const ordinal = (n) =>
  `${n}${["th", "st", "nd", "rd"][n % 100 > 10 && n % 100 < 14 ? 0 : Math.min(n % 10, 4) % 4] || "th"}`;

/** "Daily at 06:00" / "Weekly on Monday at 06:00" / "Monthly on the 1st at 06:00". */
export function describeSchedule(expression, timezone) {
  const p = cronToPicker(expression);
  const zone = timezone ? ` (${timezone})` : "";
  if (!p) return `${expression}${zone}`; // advanced/unknown shape: show the wire value
  if (p.mode === "weekly") {
    const day = WEEKDAYS.find(([id]) => id === p.weekday)?.[1] || p.weekday;
    return `Weekly on ${day} at ${p.time}${zone}`;
  }
  if (p.mode === "monthly") return `Monthly on the ${ordinal(p.monthday)} at ${p.time}${zone}`;
  return `Daily at ${p.time}${zone}`;
}

// ---- pause window (UIFPE-231) ------------------------------------------------
// The window's dates are calendar days in the schedule's OWN timezone (the
// operator means "days the feed runs there"). en-CA formatting gives ISO
// YYYY-MM-DD, so string comparison is date comparison.

export function todayInZone(timeZone) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
  } catch {
    return new Intl.DateTimeFormat("en-CA").format(new Date());
  }
}

/** How this schedule is paused right now:
 *  null                          — not paused (no window, or it passed)
 *  {kind:"indefinite"}           — the old boolean pause (enabled=false)
 *  {kind:"active", until}        — inside a pause window today
 *  {kind:"upcoming", from,until} — a window set for the future */
export function pauseState(schedule) {
  if (!schedule) return null;
  if (!schedule.paused_until)
    return schedule.enabled === false ? { kind: "indefinite" } : null;
  const today = todayInZone(schedule.timezone);
  if (today > schedule.paused_until)
    return schedule.enabled === false ? { kind: "indefinite" } : null;
  const from = schedule.paused_from || today;
  if (today < from) return { kind: "upcoming", from, until: schedule.paused_until };
  return { kind: "active", until: schedule.paused_until };
}

// ---- next-run computation (picker shapes only) ------------------------------
// EventBridge evaluates the cron in the schedule's own timezone, so "next run"
// must too: find the next wall-clock match in that zone, then convert the wall
// time to an epoch instant for display in the viewer's locale.

function zoneParts(epochMs, timeZone) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hour12: false,
  }).formatToParts(epochMs);
  const get = (type) => parts.find((p) => p.type === type)?.value;
  return {
    y: +get("year"),
    mo: +get("month"),
    d: +get("day"),
    h: +(get("hour") % 24 || get("hour")),
    mi: +get("minute"),
    dow: get("weekday").toUpperCase().slice(0, 3),
  };
}

/** Epoch ms for wall-clock y-mo-d hh:mm in a zone (two-pass offset fix —
 *  exact except inside a DST gap, where it lands within the shifted hour). */
function zonedEpoch(y, mo, d, hh, mi, timeZone) {
  let guess = Date.UTC(y, mo - 1, d, hh, mi);
  for (let i = 0; i < 2; i++) {
    const w = zoneParts(guess, timeZone);
    guess += Date.UTC(y, mo - 1, d, hh, mi) - Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi);
  }
  return guess;
}

/** Next fire instant (Date) for a picker-shaped cron, or null for other
 *  shapes / a disabled schedule. Scans day by day — bounded by 62 (monthly
 *  on the 31st can skip a month). */
export function nextRun(expression, timezone, from = new Date()) {
  const p = cronToPicker(expression);
  if (!p) return null;
  const [hh, mi] = p.time.split(":").map(Number);
  const zone = timezone || "UTC";
  const today = zoneParts(from.getTime(), zone);
  for (let offset = 0; offset <= 62; offset++) {
    // day arithmetic in UTC space, then re-read the zone's calendar day
    const probe = zoneParts(Date.UTC(today.y, today.mo - 1, today.d + offset, 12, 0), zone);
    if (p.mode === "weekly" && probe.dow !== p.weekday) continue;
    if (p.mode === "monthly" && probe.d !== p.monthday) continue;
    const at = zonedEpoch(probe.y, probe.mo, probe.d, hh, mi, zone);
    if (at > from.getTime()) return new Date(at);
  }
  return null;
}
