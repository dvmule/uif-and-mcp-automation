import { describe, expect, it } from "vitest";
import { csvCell, toCsv } from "../csv.js";

describe("csv (UIFPE-759)", () => {
  it("quotes every cell and doubles inner quotes", () => {
    expect(csvCell('Ingram "UK" feed')).toBe('"Ingram ""UK"" feed"');
    expect(csvCell(12)).toBe('"12"');
    expect(csvCell(null)).toBe('""');
    expect(csvCell(undefined)).toBe('""');
  });
  it("keeps a spreadsheet from reading a cell as a formula", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe(`"'=HYPERLINK(1)"`);
    expect(csvCell("+1")).toBe(`"'+1"`);
    expect(csvCell("@cmd")).toBe(`"'@cmd"`);
    expect(csvCell("-x")).toBe(`"'-x"`);
    expect(csvCell("-5")).toBe('"-5"'); // a negative number stays a number
  });
  it("joins rows with newlines and cells with commas", () => {
    expect(toCsv([["ID", "Export"], ["100021", "Amazon, US"]])).toBe('"ID","Export"\n"100021","Amazon, US"');
    expect(toCsv([])).toBe("");
  });
});
