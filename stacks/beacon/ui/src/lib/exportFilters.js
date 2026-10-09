// exportFilters.js (UIFPE-695)
// The Exports list's filters as one pure function. The Format / Schedule /
// Last run filters are multi-select dropdowns (MultiSelect.jsx); each keeps
// the options the author has turned OFF, so every option is on by default and
// a format or status that appears later is shown without anyone ticking it —
// the same "all" the old single selects started from.
//
// rows       catalog entries ({ job_id, name, settings.formats, mapping_config.sources })
// filters    { query, formatsOff, scheduleOff, lastOff }  — *Off are arrays of keys
// context    { runs (newest first), schedules (keyed by job_id) }

import { publishedInWindow } from "./overviewRange.js";

export const NEVER_RAN = "none";

/** The key the Last run filter uses for an export: its newest run's status, or "none". */
export function lastRunKey(jobId, runs) {
  const run = (runs || []).find((r) => r.job_id === jobId);
  return run ? run.status : NEVER_RAN;
}

/** "scheduled" when the export has a schedule record, else "manual". */
export function scheduleKey(jobId, schedules) {
  return schedules && schedules[jobId] ? "scheduled" : "manual";
}

/** UIFPE-749: the formats a catalog entry renders, lower-case. Older entries
 *  carry `settings.formats` (a list); published ones carry `settings.format`
 *  and `export_config.rendering.format` (one value) — the export page already
 *  read both, the list and its Format filter read only the list form and so
 *  showed nothing. */
export function exportFormats(entry) {
  const s = entry?.settings || {};
  const raw = Array.isArray(s.formats) && s.formats.length ? s.formats : [s.format ?? entry?.export_config?.rendering?.format];
  return [...new Set(raw.filter(Boolean).map((f) => String(f).toLowerCase()))];
}

/** The table name an author reads: the last segment of a catalog path. */
export function shortTable(path) {
  const parts = String(path || "").split(".").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

export function filterExports(rows, filters = {}, context = {}) {
  // UIFPE-754: publishedWindow = { since, until } keeps only exports whose
  // current version was published inside it; null = any time
  const { query = "", formatsOff = [], scheduleOff = [], lastOff = [], publishedWindow = null } = filters;
  const { runs = [], schedules = {} } = context;
  const q = query.trim().toLowerCase();
  return (rows || []).filter((r) => {
    if (publishedWindow && !publishedInWindow(r, publishedWindow)) return false;
    if (
      q &&
      !(
        String(r.job_id).toLowerCase().includes(q) ||
        (r.name || "").toLowerCase().includes(q) ||
        // UIFPE-237: a feed can read several sources — match any of them
        (r.mapping_config?.sources || []).some((s) => (s.path || "").toLowerCase().includes(q))
      )
    )
      return false;
    const formats = exportFormats(r);
    // shown while any of its formats is on; an export with no recorded format
    // shows only while no format is turned off (the old "All formats")
    if (formats.length ? !formats.some((f) => !formatsOff.includes(f)) : formatsOff.length > 0) return false;
    if (scheduleOff.includes(scheduleKey(r.job_id, schedules))) return false;
    if (lastOff.includes(lastRunKey(r.job_id, runs))) return false;
    return true;
  });
}

// UIFPE-722: an export whose FIRST version was published in the last day is
// "new" on the Exports list — the prototype marks what the wizard just made
export function isNewExport(entry, nowMs = Date.now()) {
  if (!entry || Number(entry.version) !== 1) return false;
  const at = Date.parse(entry.published_at);
  return !Number.isNaN(at) && nowMs - at >= 0 && nowMs - at < 864e5;
}
