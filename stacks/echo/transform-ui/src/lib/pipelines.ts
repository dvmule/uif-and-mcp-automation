// The registered pipeline configs (pipelines/base/<domain>.yaml), discovered
// by glob like repo.ts discovers mappings — so the Schedule step shows
// REAL config, not hardcoded prose. Parsed with targeted regexes (no YAML
// dependency in the bundle; these files are machine-synced and stable).
const found = import.meta.glob("../../../../../domains/*/src/apps/pipelines/base/*.yaml",
  { eager: true, query: "?raw", import: "default" }) as Record<string, string>;

// ── The cadence ladder (UIFPE-149) ──────────────────────────────────────────
// A PINNED COPY of src/apps/scripts/gold_frequencies.py's CADENCES. That Python
// module is the single source of truth — it is what the Lambda, the config
// validator and the intake compiler all read. TypeScript cannot import it, so
// qe/test_gold_frequency_parity.py fails CI if this list drifts from it in name,
// order, minutes, label or default start time. Add a rung THERE first.
//
// A pipeline's schedule is a cadence (how often) PLUS a start time (from when).
// Both are per-pipeline, which is why two daily tables can run at different
// times. One sweep rule ticks every 5 minutes and the dispatcher decides which
// pipelines are due — see the Python module's `previous_slot`.
export const GOLD_CADENCES = [
  { value: "gold-5m", minutes: 5, defaultStartTime: "00:00", label: "every 5 minutes" },
  { value: "gold-10m", minutes: 10, defaultStartTime: "00:02", label: "every 10 minutes" },
  { value: "gold-15m", minutes: 15, defaultStartTime: "00:04", label: "every 15 minutes" },
  { value: "gold-30m", minutes: 30, defaultStartTime: "00:06", label: "every 30 minutes" },
  { value: "gold-1h", minutes: 60, defaultStartTime: "00:45", label: "every hour" },
  { value: "gold-2h", minutes: 120, defaultStartTime: "00:47", label: "every 2 hours" },
  { value: "gold-4h", minutes: 240, defaultStartTime: "00:49", label: "every 4 hours" },
  { value: "gold-6h", minutes: 360, defaultStartTime: "00:51", label: "every 6 hours" },
  { value: "gold-8h", minutes: 480, defaultStartTime: "00:53", label: "every 8 hours" },
  { value: "gold-12h", minutes: 720, defaultStartTime: "00:55", label: "every 12 hours" },
  { value: "gold-24h", minutes: 1440, defaultStartTime: "06:30", label: "every 24 hours" },
] as const;

// ── Weekly: the one rung outside the day tiling (UIFPE-532) ─────────────────
// Mirrors gold_frequencies.WEEKLY_CADENCE / WEEKDAYS / DEFAULT_WEEKLY_DAYS; the
// parity test pins all of them. A weekly entity runs on a SET of UTC weekdays
// at one time of day, so its slots are a 7-day grid, not "start + k x interval".
export const GOLD_WEEKLY = { value: "gold-weekly", minutes: 10080, defaultStartTime: "06:30", label: "weekly" } as const;
export const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;
export type Weekday = typeof WEEKDAYS[number];
export const DEFAULT_WEEKLY_DAYS: readonly Weekday[] = ["MON"];

export type GoldFrequency = typeof GOLD_CADENCES[number]["value"] | typeof GOLD_WEEKLY["value"];
type Rung = { value: GoldFrequency; minutes: number; defaultStartTime: string; label: string };

export const DEFAULT_FREQUENCY: GoldFrequency = "gold-24h";
export const DEFAULT_COOLDOWN_MINUTES = 30;

const MINUTES_PER_DAY = 1440;

// Pre-UIFPE-149 cadence names, still present in any environment whose configs
// have not been re-synced. Mirrors gold_frequencies.LEGACY_ALIASES so the UI
// reports the cadence a config ACTUALLY runs at rather than an unknown string.
const LEGACY_ALIASES: Record<string, GoldFrequency> = {
  "gold": "gold-24h",
  "gold-hourly": "gold-1h",
  "gold-60m": "gold-1h",
};

export const normalizeFrequency = (raw: string | undefined): GoldFrequency => {
  if (!raw) return DEFAULT_FREQUENCY;
  const aliased = LEGACY_ALIASES[raw] ?? raw;
  return aliased === GOLD_WEEKLY.value || GOLD_CADENCES.some((c) => c.value === aliased)
    ? aliased as GoldFrequency
    : DEFAULT_FREQUENCY;
};

export const isWeekly = (frequency: string | undefined): boolean =>
  frequency === GOLD_WEEKLY.value;

export const cadenceFor = (frequency: GoldFrequency): Rung | null =>
  frequency === GOLD_WEEKLY.value
    ? GOLD_WEEKLY
    : (GOLD_CADENCES.find((c) => c.value === frequency) ?? null);

/** A weekly entity's day set in ISO order, or null if unusable (mirrors
 *  gold_frequencies.parse_weekly_days: case-insensitive, duplicates dropped,
 *  empty is null). */
export const parseWeeklyDays = (value: unknown): Weekday[] | null => {
  if (!Array.isArray(value)) return null;
  const seen = new Set<Weekday>();
  for (const v of value) {
    if (typeof v !== "string") return null;
    const token = v.trim().toUpperCase() as Weekday;
    if (!WEEKDAYS.includes(token)) return null;
    seen.add(token);
  }
  if (seen.size === 0) return null;
  return WEEKDAYS.filter((d) => seen.has(d));
};

export const defaultStartTimeFor = (frequency: GoldFrequency): string =>
  cadenceFor(frequency)?.defaultStartTime ?? "00:00";

// ── Start times and slots ───────────────────────────────────────────────────
// Mirrors gold_frequencies.py. The shared vector in
// qe/fixtures/schedule-slots.json is asserted by BOTH sides, so these cannot
// drift into disagreeing about when a pipeline runs.

/**
 * "HH:MM" -> minutes since midnight UTC, or null if unparseable.
 *
 * STRICTLY one or two digits per part, and both parts required. The rule is
 * tight on purpose: the Python twin in gold_frequencies.py is what the
 * dispatcher actually runs, and loose parsing is where the two drifted — ":30"
 * was rejected there (int("") raises) but accepted here as 00:30, because
 * Number("") is 0. Anything Number() would coerce — "", " ", "6e1", "+6", "0x6"
 * — has to be rejected for the two to agree.
 */
export const parseTimeOfDay = (value: string | undefined): number | null => {
  if (typeof value !== "string") return null;
  const parts = value.trim().split(":");
  if (parts.length !== 2 || !parts.every((p) => /^\d{1,2}$/.test(p))) return null;
  const hh = Number(parts[0]), mm = Number(parts[1]);
  if (hh > 23 || mm > 59) return null;
  return hh * 60 + mm;
};

export const formatTimeOfDay = (minutes: number): string => {
  const h = Math.floor(minutes / 60) % 24, m = ((minutes % 60) + 60) % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
};

/** Normalizes a possibly-absent/invalid start time to a usable one. */
export const normalizeStartTime = (
  raw: string | undefined, frequency: GoldFrequency,
): string => (parseTimeOfDay(raw) === null ? defaultStartTimeFor(frequency)
  : (raw as string).trim());

/**
 * Every slot in a day, as minutes since midnight, ascending.
 *
 * The slots are the times congruent to the start time modulo the interval —
 * which tiles the day exactly because every interval divides 1440. So 06:30 on
 * gold-8h gives 06:30 / 14:30 / 22:30, and the wrap back to 06:30 is a full 8
 * hours rather than a short night.
 */
export const slotMinutes = (
  startTime: string | undefined, frequency: GoldFrequency,
): number[] => {
  const interval = cadenceFor(frequency)?.minutes;
  const anchor = parseTimeOfDay(startTime);
  if (!interval || anchor === null) return [];
  if (isWeekly(frequency)) return [anchor];   // at most one slot on a run day
  const out: number[] = [];
  for (let t = anchor % interval; t < MINUTES_PER_DAY; t += interval) out.push(t);
  return out;
};

/**
 * The most recent scheduled instant at or before `now`.
 *
 * A negative offset from midnight is correct and intended — it lands on
 * yesterday's slot when `now` precedes today's first one. Getting that wrong
 * (clamping to midnight) would make a pipeline look due on every tick until its
 * first slot of the day.
 */
const weekdayOf = (d: Date): Weekday => WEEKDAYS[(d.getUTCDay() + 6) % 7];

/** The most recent (weekday in weeklyDays, startTime) instant at or before
 *  `now`, over the 7-day UTC grid. Mirrors gold_frequencies.previous_weekly_slot. */
export const previousWeeklySlot = (
  now: Date, startTime: string | undefined, weeklyDays?: unknown,
): Date | null => {
  const days = parseWeeklyDays(weeklyDays) ?? DEFAULT_WEEKLY_DAYS;
  const anchor = parseTimeOfDay(startTime);
  if (anchor === null) return null;
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  for (let back = 0; back <= 7; back++) {
    const cand = new Date(midnight - back * 86_400_000 + anchor * 60_000);
    if (days.includes(weekdayOf(cand)) && cand.getTime() <= now.getTime()) return cand;
  }
  return null;
};

export const nextWeeklySlot = (
  now: Date, startTime: string | undefined, weeklyDays?: unknown,
): Date | null => {
  const days = parseWeeklyDays(weeklyDays) ?? DEFAULT_WEEKLY_DAYS;
  const anchor = parseTimeOfDay(startTime);
  if (anchor === null) return null;
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  for (let ahead = 0; ahead <= 7; ahead++) {
    const cand = new Date(midnight + ahead * 86_400_000 + anchor * 60_000);
    if (days.includes(weekdayOf(cand)) && cand.getTime() > now.getTime()) return cand;
  }
  return null;
};

export const previousSlot = (
  now: Date, startTime: string | undefined, frequency: GoldFrequency,
  weeklyDays?: unknown,
): Date | null => {
  if (isWeekly(frequency)) return previousWeeklySlot(now, startTime, weeklyDays);
  const interval = cadenceFor(frequency)?.minutes;
  const anchor = parseTimeOfDay(startTime);
  if (!interval || anchor === null) return null;
  const nowMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  const phase = anchor % interval;
  const offset = ((nowMinutes - phase) % interval + interval) % interval;
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
                            now.getUTCDate());
  return new Date(midnight + (nowMinutes - offset) * 60_000);
};

export const nextSlot = (
  now: Date, startTime: string | undefined, frequency: GoldFrequency,
  weeklyDays?: unknown,
): Date | null => {
  if (isWeekly(frequency)) return nextWeeklySlot(now, startTime, weeklyDays);
  const prev = previousSlot(now, startTime, frequency);
  const interval = cadenceFor(frequency)?.minutes;
  if (!prev || !interval) return null;
  return new Date(prev.getTime() + interval * 60_000);
};

/**
 * The times of day this schedule runs at, for the wizard's preview — the whole
 * reason the slot maths exists on this side. Long ladders are elided rather than
 * printing 288 entries.
 */
export const runTimesPreview = (
  startTime: string | undefined, frequency: GoldFrequency, max = 6,
): { times: string[]; elided: number } => {
  const slots = slotMinutes(startTime, frequency);
  return { times: slots.slice(0, max).map(formatTimeOfDay),
           elided: Math.max(0, slots.length - max) };
};

// ── Registered pipelines ────────────────────────────────────────────────────

export interface PipelineInfo {
  /** The pipeline's gold_id — equal to the entity (the gold table's name)
   *  and to the yaml's filename stem, both enforced by sync_pipelines. */
  goldId: string;
  domain: string;
  frequency: GoldFrequency;
  startTime: string;
  /** UTC run days on the weekly rung (UIFPE-532); Monday when the YAML names
   *  none. Present on every rung so seeding is uniform; ignored off-weekly. */
  weeklyDays: Weekday[];
  raw: string;
}

/** `weekly_days: [TUE, THU]` from a pipeline YAML (a flow list, as the delta
 *  compiler writes it); the runtime default when absent or unusable. */
export const parseWeeklyDaysLine = (raw: string): Weekday[] => {
  const m = /^\s{2}weekly_days:\s*\[([^\]]*)\]/m.exec(raw);
  const days = m ? parseWeeklyDays(m[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean)) : null;
  return days ?? [...DEFAULT_WEEKLY_DAYS];
};

const parse = (path: string, raw: string): PipelineInfo => {
  const frequency = normalizeFrequency(
    /^\s{2}frequency:\s*(\S+)/m.exec(raw)?.[1]);
  return {
    goldId: /^gold_id:\s*(\S+)/m.exec(raw)?.[1]
      ?? path.split("/").pop()?.replace(/\.ya?ml$/, "") ?? "",
    domain: /^domain:\s*(\S+)/m.exec(raw)?.[1]
      ?? path.split("/src/")[0].split("/").pop() ?? "",
    frequency,
    // Quotes are optional in YAML and the compiler writes them, so strip them.
    startTime: normalizeStartTime(
      /^\s{2}start_time:\s*"?([^"\s]+)"?/m.exec(raw)?.[1], frequency),
    weeklyDays: parseWeeklyDaysLine(raw),
    raw,
  };
};

export const PIPELINES: PipelineInfo[] =
  Object.entries(found).map(([p, raw]) => parse(p, raw));

/** The pipeline for one ENTITY, by gold_id (the gold table's own name —
 *  pass `table.split(".").pop()`). Never keyed by domain: a domain can carry
 *  several pipelines (product + marketing_copy since UIFPE-252), and a
 *  domain-keyed find silently returned whichever globbed first — which is
 *  how a routine product edit nearly rescheduled product's live 06:30 run. */
export const pipelineFor = (goldId: string | null): PipelineInfo | null =>
  PIPELINES.find((p) => p.goldId === goldId) ?? null;

// The dropdown's options. The cadence says how often; the start-time field next
// to it says from when. The weekly rung closes the list (UIFPE-532): it is the
// one option that also asks WHICH days.
export const FREQUENCIES = [...GOLD_CADENCES, GOLD_WEEKLY].map((c) => ({
  value: c.value as GoldFrequency,
  label: c.label,
}));

/** "Tue, Thu" for the schedule copy; the whole week reads "every day". */
export const formatWeekdays = (days: readonly Weekday[]): string =>
  days.length === 7 ? "every day"
    : days.map((d) => d.charAt(0) + d.slice(1).toLowerCase()).join(", ");
