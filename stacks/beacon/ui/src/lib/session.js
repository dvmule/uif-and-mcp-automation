// session.js — the RELAY shell's login session, read-only (UIFPE-487).
//
// The shell (the Echo app at this origin's root) signs users in and persists
// the session to sessionStorage under "echo-auth" (zustand persist envelope).
// Beacon lives on the SAME origin and is reached by SAME-TAB navigation, so
// that entry is visible here: Beacon never logs anyone in, it only reads the
// session, sends its token to the API, and bounces to the shell's login when
// there is none. sessionStorage dies with the tab — that is the shell's
// deliberate session model, inherited unchanged.

const STORAGE_KEY = "echo-auth";

/** Parse the shell's persisted session envelope. Pure — unit-tested.
 *  Returns {token, username, expiresAt} or null (absent/corrupt/expired). */
export function parseShellSession(raw, now = Date.now()) {
  if (!raw) return null;
  let state;
  try {
    state = JSON.parse(raw)?.state;
  } catch {
    return null;
  }
  const { token, username, expiresAt } = state || {};
  if (!token || !expiresAt) return null;
  const expiry = Date.parse(expiresAt);
  // Unparseable expiry = corrupt or hand-edited: fail closed, like the shell.
  if (Number.isNaN(expiry) || expiry <= now) return null;
  return { token, username: username || "", expiresAt };
}

/** The current shell session, or null. Storage can throw in private windows —
 *  treated as signed out. */
export function readShellSession() {
  try {
    return parseShellSession(sessionStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

/** The shell login URL that returns to `returnTo` (a same-origin path) after
 *  sign-in. Pure — unit-tested. */
export function shellLoginUrl(returnTo) {
  return `/login?return=${encodeURIComponent(returnTo)}`;
}

/** This page's full same-origin path (path + query + hash), the shape the
 *  shell login's `return` parameter accepts. */
export function currentPanelPath() {
  const { pathname, search, hash } = window.location;
  return `${pathname}${search}${hash}`;
}

/** Leave for the shell login, coming back here afterwards. */
export function redirectToLogin() {
  window.location.replace(shellLoginUrl(currentPanelPath()));
}

/** Sign out (UIFPE-548): drop the shared shell session from this tab. Both
 *  apps read the same entry on the same origin, so this signs the tab out of
 *  Echo and Beacon together — the shell's own logout does exactly this.
 *  Client-side only: the stateless token stays valid until its expiry (the
 *  accepted interim model; real revocation arrives with SSO). Storage can
 *  throw in private windows — already signed out is a fine outcome. */
export function clearShellSession(storage) {
  try {
    (storage ?? sessionStorage).removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}
