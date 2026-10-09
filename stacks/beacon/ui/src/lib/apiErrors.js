// apiErrors.js (UIFPE-750)
// The wizard's Check filter and Validate hand an API failure to the author as
// an issue `{ where: "api", message }` — and `message` was the raw server
// string ("Config is missing required key 'name'", "HTTP 500 …"). This maps
// the shapes we know to one plain sentence; the raw text stays available as
// `detail` so a reader who needs it can still open it. Unknown messages pass
// through unchanged with no detail (nothing to add).

const RULES = [
  {
    re: /missing required key\s+'?"?(\w+)'?"?/i,
    text: (m) => `The check could not run: the config it was sent has no "${m[1]}". Finish the earlier steps, then try again.`,
  },
  {
    re: /\b(failed to fetch|networkerror|network error|load failed|ECONNREFUSED|timed? ?out)\b/i,
    text: () => "Beacon could not reach the API. Check your connection and try again.",
  },
  {
    re: /\b(401|403)\b|sign in to use beacon|no session token|unauthori[sz]ed|forbidden/i,
    text: () => "Your session has expired or lacks access — sign in again, then retry.",
  },
  {
    re: /\b(50[0-4])\b|internal server error|bad gateway|service unavailable|gateway time-?out/i,
    text: () => "The service hit an error while checking. Try again in a moment; if it keeps happening, tell the platform team.",
  },
  {
    re: /\b(422|validation error|unprocessable)\b/i,
    text: () => "The API refused the request as malformed. The detail below says which field.",
  },
];

/** @returns {{ text: string, detail: string | null }} */
export function friendlyApiError(message) {
  const raw = String(message ?? "").trim();
  if (!raw) return { text: "The check failed for an unknown reason. Try again.", detail: null };
  for (const rule of RULES) {
    const m = raw.match(rule.re);
    if (m) return { text: rule.text(m), detail: raw };
  }
  return { text: raw, detail: null };
}
