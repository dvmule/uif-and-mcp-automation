import { describe, expect, it } from "vitest";
import { friendlyApiError } from "../apiErrors.js";

describe("friendlyApiError (UIFPE-750)", () => {
  it("names the missing config key and keeps the raw text as detail", () => {
    const r = friendlyApiError("Config is missing required key 'name'");
    expect(r.text).toContain('no "name"');
    expect(r.detail).toBe("Config is missing required key 'name'");
  });
  it("reads a network failure, an auth failure and a server error", () => {
    expect(friendlyApiError("TypeError: Failed to fetch").text).toMatch(/could not reach the API/);
    expect(friendlyApiError("401: sign in to use Beacon (no session token)").text).toMatch(/sign in again/);
    expect(friendlyApiError("HTTP 502 Bad Gateway").text).toMatch(/hit an error/);
  });
  it("passes an unknown message through with no detail", () => {
    expect(friendlyApiError("alias 'p' is not joined")).toEqual({ text: "alias 'p' is not joined", detail: null });
  });
  it("says so when there is no message at all", () => {
    expect(friendlyApiError("").detail).toBeNull();
    expect(friendlyApiError(undefined).text).toMatch(/unknown reason/);
  });
});
