// Thin client for the control panel API.
//
// Base URL resolution:
//   VITE_BEACON_API_URL  set at build time when the UI is served from a host
//                        that does NOT route /api/* to the Beacon API — e.g.
//                        the shared RELAY CloudFront (/m/beacon/ deploy).
//   default "/api"       same-origin: the Vite dev proxy locally, the
//                        standalone distribution's /api/* behavior deployed.
const BASE = (import.meta.env.VITE_BEACON_API_URL || "/api").replace(/\/$/, "");

import { readShellSession, redirectToLogin } from "./lib/session.js";

// UIFPE-418: every call reports its in-flight state to a tiny subscribable
// store (useSyncExternalStore shape) — the shell's TopLoader renders off it,
// so a cold Lambda (~1-2 s) shows progress instead of a dead-looking click,
// globally and automatically for every current and future endpoint.
let inFlight = 0;
const listeners = new Set();
function notify() {
  for (const fn of listeners) fn();
}
export const apiActivity = {
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  isBusy: () => inFlight > 0,
};

async function request(path, options = {}) {
  inFlight += 1;
  notify();
  try {
    // UIFPE-487: the API verifies RELAY shell session tokens. Signed out on a
    // deployment without the auth switch (local dev), there is no session and
    // no header — the API is open there and answers anyway.
    const session = readShellSession();
    const response = await fetch(`${BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(session ? { Authorization: `Bearer ${session.token}` } : {}),
      },
      ...options,
    });
    const body = await response.json().catch(() => null);
    if (response.status === 401) {
      // Expired or revoked mid-use: round-trip through the shell login and
      // come back to this page, instead of failing screen by screen.
      redirectToLogin();
      throw new Error("session expired — signing in again");
    }
    if (!response.ok) {
      const detail = body && body.detail ? body.detail : response.statusText;
      throw new Error(detail);
    }
    return body;
  } finally {
    inFlight -= 1;
    notify();
  }
}

export const api = {
  health: () => request("/health"),
  sources: (formats) =>
    request(`/registries/sources${formats && formats.length ? `?formats=${formats.join(",")}` : ""}`),
  // UIFPE-229: domain-first source discovery, live from the Glue catalog
  domains: () => request("/registries/domains"),
  domainTables: (domain) =>
    request(`/registries/domains/${encodeURIComponent(domain)}/tables`),
  // UIFPE-581: the declared Gold relationships the wizard prefills joins from
  relationships: () => request("/registries/relationships"),
  sourceColumns: (sourceId) =>
    request(`/registries/sources/${encodeURIComponent(sourceId)}/columns`),
  formats: () => request("/registries/formats"),
  channels: () => request("/registries/channels"),
  destinations: () => request("/destinations"),
  // UIFPE-558: the partner's XSD — staged on upload, frozen at publish
  uploadSchema: (filename, content) =>
    request("/schemas", { method: "POST",
                          body: JSON.stringify({ filename, content }) }),
  getSchema: (id) => request(`/schemas/${id}`),
  // UIFPE-660: an XSD built from a sample XML — returned, not stored
  inferSchema: (filename, content) =>
    request("/schemas/infer", { method: "POST",
                                body: JSON.stringify({ filename, content }) }),
  testDestination: (id) =>
    request(`/destinations/${id}/test`, { method: "POST" }),
  deleteDestination: (id) =>
    request(`/destinations/${id}`, { method: "DELETE" }),
  updateDestination: (id, body) =>
    request(`/destinations/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  addDestination: (body) =>
    request("/destinations", { method: "POST", body: JSON.stringify(body) }),
  convert: (sql, dialect) =>
    request("/convert", { method: "POST", body: JSON.stringify({ sql, dialect }) }),
  // UIFPE-609: `format` (csv/json/xml) lets the validator refuse a bare
  // list/record column the CSV writer could not carry
  validateMapping: (config, format) =>
    request(`/validate/mapping-config${format ? `?format=${encodeURIComponent(format)}` : ""}`, {
      method: "POST",
      body: JSON.stringify(config),
    }),
  validateExport: (config) =>
    request("/validate/export-config", { method: "POST", body: JSON.stringify(config) }),
  saveDraft: (body) =>
    request("/drafts", { method: "POST", body: JSON.stringify(body) }),
  // validated => published (UIFPE-195): submit returns the published version
  submitDraft: (id) =>
    request(`/drafts/${id}/submit`, { method: "POST", body: "{}" }),
  // UIFPE-446: retired exports are hidden unless the caller asks for them
  catalog: (includeRetired) =>
    request(`/catalog${includeRetired ? "?include_retired=true" : ""}`),
  catalogVersions: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/versions`),
  // UIFPE-446: soft delete — versions, runs, and delivered files all stay
  retireExport: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/retire`, { method: "POST", body: "{}" }),
  unretireExport: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/unretire`, { method: "POST", body: "{}" }),
  // D22: omitting `version` runs the promoted (latest) version; pass one to pin
  startRun: (jobId, body = {}) =>
    request(`/catalog/${encodeURIComponent(jobId)}/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // UIFPE-199: a draft carrying the existing export id — submit publishes vN+1
  editExport: (jobId, body) =>
    request(`/catalog/${encodeURIComponent(jobId)}/edit`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // UIFPE-207: per-export schedules. GET 404s when none — callers treat that
  // as "no schedule", not an error.
  getSchedule: (jobId) => request(`/catalog/${encodeURIComponent(jobId)}/schedule`),
  putSchedule: (jobId, body) =>
    request(`/catalog/${encodeURIComponent(jobId)}/schedule`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSchedule: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/schedule`, { method: "DELETE" }),
  // all schedule records in one call — the Exports list needs one per row
  listSchedules: () => request("/schedules"),
  runStatus: (runId) => request(`/runs/${encodeURIComponent(runId)}`),
  // UIFPE-203: aborts the execution + its ECS task, records the run as stopped
  stopRun: (runId) =>
    request(`/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" }),
  // UIFPE-220: gold drift — does each export's frozen config still match the
  // live catalog? Checked on the panel, before the schedule finds out.
  drift: () => request("/drift"),
  jobDrift: (jobId) => request(`/catalog/${encodeURIComponent(jobId)}/drift`),
  // limit: the API returns the newest 50 unless asked for more (200 max) —
  // the Runs screen asks for 200 and pages client-side (UIFPE-442)
  runHistory: (jobId, limit) => {
    const params = new URLSearchParams();
    if (jobId) params.set("job_id", jobId);
    if (limit) params.set("limit", limit);
    const qs = params.toString();
    return request(`/runs${qs ? `?${qs}` : ""}`);
  },
  // UIFPE-610: counts by status of EVERY recorded run in [since, until) —
  // the Overview's Run outcomes; runHistory's newest-N is not a count
  runSummary: (since, until) => {
    const params = new URLSearchParams({ since, until });
    return request(`/runs/summary?${params.toString()}`);
  },
  batchFiles: (jobId, batchId) =>
    request(`/catalog/${jobId}/batches/${encodeURIComponent(batchId)}/files`),
  // UIFPE-168: extraction mode (versioned config) + stored watermark (runtime
  // state); reset forces the next run to re-baseline with a full extract
  extraction: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/extraction`),
  resetWatermark: (jobId) =>
    request(`/catalog/${encodeURIComponent(jobId)}/extraction/watermark`, {
      method: "DELETE",
    }),
  // UIFPE-441: live Gold data for the Filter step, via Athena. Values: the
  // column's most common values (a `reason` instead of values when the column
  // cannot be suggested). Preview: POST starts the executions (202 + id, or
  // 200 {status: refused}), GET polls, DELETE stops them.
  columnValues: (sourceId, column) =>
    request(
      `/registries/sources/${encodeURIComponent(sourceId)}/columns/${encodeURIComponent(column)}/values`
    ),
  // UIFPE-514: the table's cardinality profile (row count + approximate
  // distinct count per column) decides which text attributes the picker
  // offers. Asynchronous and persisted server-side: GET answers a persisted
  // profile (done) or absent/unavailable without touching Athena, POST starts
  // one execution (202 + id, or 200 done when a fresh profile exists), the
  // status GET polls it.
  getProfile: (sourceId) => request(`/registries/sources/${encodeURIComponent(sourceId)}/profile`),
  startProfile: (sourceId) =>
    request(`/registries/sources/${encodeURIComponent(sourceId)}/profile`, { method: "POST" }),
  profileStatus: (sourceId, profileId) =>
    request(`/registries/sources/${encodeURIComponent(sourceId)}/profile/${encodeURIComponent(profileId)}`),
  startPreview: (body) =>
    request("/preview/filter", { method: "POST", body: JSON.stringify(body) }),
  previewStatus: (previewId) => request(`/preview/${encodeURIComponent(previewId)}`),
  cancelPreview: (previewId) =>
    request(`/preview/${encodeURIComponent(previewId)}`, { method: "DELETE" }),
  // UIFPE-490: every matched row as one CSV — the same body as startPreview.
  // POST starts the Athena execution (202 + id, or 200 {status: refused}),
  // GET polls (a done result carries a 15-minute signed link), DELETE stops it.
  startPreviewDownload: (body) =>
    request("/preview/download", { method: "POST", body: JSON.stringify(body) }),
  previewDownloadStatus: (downloadId) =>
    request(`/preview/download/${encodeURIComponent(downloadId)}`),
  cancelPreviewDownload: (downloadId) =>
    request(`/preview/download/${encodeURIComponent(downloadId)}`, { method: "DELETE" }),
};
