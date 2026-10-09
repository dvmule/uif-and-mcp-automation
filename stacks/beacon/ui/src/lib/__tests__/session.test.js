// session.test.js (UIFPE-487) — the pure halves of the shell-session reader:
// parsing the shell's persisted envelope (absent / corrupt / expired all read
// as signed out) and building the login round-trip URL.

import { describe, expect, it } from "vitest";
import { clearShellSession, parseShellSession, shellLoginUrl } from "../session.js";

const NOW = Date.parse("2026-09-21T10:00:00Z");

function envelope(state) {
  return JSON.stringify({ state, version: 0 });
}

describe("parseShellSession", () => {
  it("returns the session while the token is valid", () => {
    const raw = envelope({
      token: "abc.def",
      username: "karthik",
      expiresAt: "2026-09-21T18:00:00Z",
    });
    expect(parseShellSession(raw, NOW)).toEqual({
      token: "abc.def",
      username: "karthik",
      expiresAt: "2026-09-21T18:00:00Z",
    });
  });

  it("reads absent, empty and corrupt storage as signed out", () => {
    expect(parseShellSession(null, NOW)).toBeNull();
    expect(parseShellSession("", NOW)).toBeNull();
    expect(parseShellSession("not-json", NOW)).toBeNull();
    expect(parseShellSession(envelope(undefined), NOW)).toBeNull();
    expect(parseShellSession(envelope({ username: "k" }), NOW)).toBeNull();
  });

  it("fails closed on expired or unparseable expiry", () => {
    const expired = envelope({
      token: "t", username: "k", expiresAt: "2026-09-21T09:59:59Z",
    });
    expect(parseShellSession(expired, NOW)).toBeNull();
    const garbage = envelope({
      token: "t", username: "k", expiresAt: "not-a-date",
    });
    expect(parseShellSession(garbage, NOW)).toBeNull();
  });

  it("tolerates a session without a username", () => {
    const raw = envelope({ token: "t", expiresAt: "2026-09-21T18:00:00Z" });
    expect(parseShellSession(raw, NOW)).toEqual({
      token: "t", username: "", expiresAt: "2026-09-21T18:00:00Z",
    });
  });
});

describe("shellLoginUrl", () => {
  it("round-trips the full panel path, hash included", () => {
    expect(shellLoginUrl("/m/beacon/index.html#/m/beacon/exports/100005"))
      .toBe("/login?return=%2Fm%2Fbeacon%2Findex.html%23%2Fm%2Fbeacon%2Fexports%2F100005");
  });
});

describe("clearShellSession (UIFPE-548)", () => {
  it("removes the shell's session entry", () => {
    const removed = [];
    clearShellSession({ removeItem: (k) => removed.push(k) });
    expect(removed).toEqual(["echo-auth"]);
  });

  it("treats a throwing storage as already signed out", () => {
    const broken = { removeItem: () => { throw new Error("private window"); } };
    expect(() => clearShellSession(broken)).not.toThrow();
  });
});
