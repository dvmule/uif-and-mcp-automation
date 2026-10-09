// One place where a status VALUE becomes a colour. Every status vocabulary in
// the transform layer shares six tones, so without a single mapping the same
// value drifts between "warn" on one screen and "ok" on the next.
//
// Rules this encodes:
//  · The literal status word is always rendered. Colour is never the only
//    signal (WCAG 1.4.1) — an icon-only or colour-only pill is not offered.
//  · Unknown values fall back to a neutral tone and are shown VERBATIM rather
//    than guessed at. The reconcile job writes parameterised statuses
//    (`SKIPPED (dry-run (orphans=1204))`), so exact-match lookup would miss
//    them; matching is prefix-based for that reason.
//  · A skip is neutral, not a warning: the dispatcher skips a run when no
//    source changed, which is the expected steady state.

import type { ReactNode } from "react";

export type PillTone = "info" | "ok" | "warn" | "bad" | "live" | "mute";

export function StatusPill({ tone = "info", label, why, title }: {
  tone?: PillTone;
  label: string;
  /** Reason code or evidence — an identifier, rendered monospace. */
  why?: string | null;
  title?: string;
}): ReactNode {
  // "info" is index.css's base .pill; the others are modifier classes.
  const cls = tone === "info" ? "pill" : `pill ${tone}`;
  return (
    <span className={cls} title={title}>
      {label}
      {why ? <span className="why">{why}</span> : null}
    </span>
  );
}

/** Terminal statuses come from the run record; live ones from the job service.
 *  Prefix match so `SKIPPED (reason)` lands with SKIPPED. */
export function runStatusTone(status: string): PillTone {
  const s = (status ?? "").trim().toUpperCase();
  if (s.startsWith("SUCCEEDED")) return "ok";
  if (s.startsWith("FAILED") || s.startsWith("ERROR") || s.startsWith("TIMEOUT")) return "bad";
  if (s.startsWith("RUNNING") || s.startsWith("STARTING")) return "live";
  if (s.startsWith("SKIPPED") || s.startsWith("STOPPED") || s.startsWith("STOPPING")) return "mute";
  return "mute";
}

/** Readiness-check outcomes (the preflight validator's PASS/WARN/FAIL/INFO). */
export function checkResultTone(result: string): PillTone {
  switch ((result ?? "").trim().toUpperCase()) {
    case "PASS": return "ok";
    case "WARN": return "warn";
    case "FAIL": return "bad";
    case "INFO": return "info";
    default: return "mute";
  }
}
