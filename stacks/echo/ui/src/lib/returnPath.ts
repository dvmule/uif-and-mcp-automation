/** UIFPE-487: the login page's `?return=` parameter — how a gated sibling
 *  static app (the Beacon panel at /m/beacon) round-trips through the shell's
 *  login. RequireAuth keeps using router state for in-app returns; `return`
 *  exists for destinations OUTSIDE the SPA, reached by a full page load.
 *
 *  Only a same-origin module path is accepted: it must start with "/m/",
 *  which rules out other origins ("https://…", "//evil.example") and the
 *  shell's own routes (those go through router state). Anything else returns
 *  null and login falls back to its normal behaviour — never an open
 *  redirect. */
export function sameOriginReturnPath(search: string): string | null {
  const raw = new URLSearchParams(search).get('return')
  if (!raw) return null
  // Backslashes normalise to slashes in some URL parsers — refuse them.
  if (raw.includes('\\')) return null
  if (!/^\/m\/[A-Za-z0-9-]/.test(raw)) return null
  return raw
}
