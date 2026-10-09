import { describe, expect, it } from "vitest";
import { chipValueText, isActive, offerable, resetValue, startValue } from "../filterChips.js";

const fmt = (selected) => ({
  key: "format",
  name: "Format",
  kind: "multi",
  items: [
    { key: "csv", label: "CSV" },
    { key: "json", label: "JSON" },
    { key: "xml", label: "XML" },
  ],
  selected,
  start: ["json"],
});
const pub = (value) => ({
  key: "published",
  name: "Published",
  kind: "single",
  items: [
    { key: "any", label: "Any time" },
    { key: "1d", label: "Last 24 hours" },
    { key: "7d", label: "Last 7 days" },
  ],
  value,
  defaultValue: "any",
  start: "7d",
});
const retired = (on) => ({ key: "retired", name: "Retired exports", kind: "toggle", on });

describe("filterChips (UIFPE-762)", () => {
  it("reads at most two picks, then +N, and says none when nothing is picked", () => {
    expect(chipValueText([])).toBe("none");
    expect(chipValueText(["CSV"])).toBe("CSV");
    expect(chipValueText(["CSV", "JSON"])).toBe("CSV, JSON");
    expect(chipValueText(["succeeded", "failed", "running", "skipped"])).toBe("succeeded, failed +2");
  });

  it("gives a chip only to a filter that narrows the list", () => {
    expect(isActive(fmt(["csv", "json", "xml"]))).toBe(false);
    expect(isActive(fmt(["csv"]))).toBe(true);
    expect(isActive(fmt([]))).toBe(true); // nothing picked hides everything — still a filter
    expect(isActive({ ...fmt([]), items: [] })).toBe(false); // no options yet
    expect(isActive(pub("any"))).toBe(false);
    expect(isActive(pub("7d"))).toBe(true);
    expect(isActive(retired(false))).toBe(false);
    expect(isActive(retired(true))).toBe(true);
  });

  it("resets to every option, the default value, or off", () => {
    expect(resetValue(fmt(["csv"]))).toEqual(["csv", "json", "xml"]);
    expect(resetValue(pub("7d"))).toBe("any");
    expect(resetValue(retired(true))).toBe(false);
  });

  it("starts from the caller's choice when it exists, else the first option", () => {
    expect(startValue(fmt(["csv", "json", "xml"]))).toEqual(["json"]);
    expect(startValue({ ...fmt([]), start: ["pdf"] })).toEqual(["csv"]);
    expect(startValue({ ...fmt([]), start: undefined })).toEqual(["csv"]);
    expect(startValue(pub("any"))).toBe("7d");
    expect(startValue({ ...pub("any"), start: undefined })).toBe("1d"); // skips the default
    expect(startValue(retired(false))).toBe(true);
  });

  it("offers every filter except a multi with no options yet", () => {
    expect(offerable([fmt([]), { ...fmt([]), key: "x", items: [] }, pub("any"), retired(false)]).map((d) => d.key)).toEqual([
      "format",
      "published",
      "retired",
    ]);
  });
});
