import { describe, expect, it } from "vitest";
import { NEVER_RAN, exportFormats, filterExports, lastRunKey, scheduleKey, shortTable,
  isNewExport,
} from "../exportFilters.js";

const ROWS = [
  { job_id: "100021", name: "Amazon US Product Catalog", settings: { formats: ["csv"] },
    mapping_config: { sources: [{ path: "glue_catalog.uif_echo_dev_gold_product.product" }] } },
  // UIFPE-749: a published entry carries one `settings.format`, upper-case
  { job_id: "100022", name: "Ingram UK Weekly Catalog", settings: { format: "XML" },
    export_config: { rendering: { format: "XML" } },
    mapping_config: { sources: [{ path: "glue_catalog.uif_echo_dev_gold_price.price" }] } },
  { job_id: "100023", name: "Trade Price", settings: {}, mapping_config: { sources: [] } },
];
const RUNS = [
  { job_id: "100021", status: "failed" },
  { job_id: "100021", status: "succeeded" },
  { job_id: "100022", status: "succeeded" },
];
const SCHEDULES = { "100021": { job_id: "100021" } };
const ids = (rows) => rows.map((r) => r.job_id);
const ctx = { runs: RUNS, schedules: SCHEDULES };

describe("filterExports", () => {
  it("shows everything when nothing is turned off", () => {
    expect(ids(filterExports(ROWS, {}, ctx))).toEqual(["100021", "100022", "100023"]);
  });
  it("finds by name, id or source path", () => {
    expect(ids(filterExports(ROWS, { query: "ingram" }, ctx))).toEqual(["100022"]);
    expect(ids(filterExports(ROWS, { query: "100023" }, ctx))).toEqual(["100023"]);
    expect(ids(filterExports(ROWS, { query: "gold_price" }, ctx))).toEqual(["100022"]);
  });
  it("hides an export whose every format is off, and format-less ones once any format is off", () => {
    expect(ids(filterExports(ROWS, { formatsOff: ["csv"] }, ctx))).toEqual(["100022"]);
    // UIFPE-749: the singular settings.format counts too
    expect(ids(filterExports(ROWS, { formatsOff: ["xml"] }, ctx))).toEqual(["100021"]);
  });
  it("filters scheduled vs manual", () => {
    expect(ids(filterExports(ROWS, { scheduleOff: ["manual"] }, ctx))).toEqual(["100021"]);
    expect(ids(filterExports(ROWS, { scheduleOff: ["scheduled"] }, ctx))).toEqual(["100022", "100023"]);
  });
  it("keeps only exports whose current version was published in the window (UIFPE-754)", () => {
    const rows = [
      { ...ROWS[0], published_at: "2026-09-29T05:00:00Z" },
      { ...ROWS[1], published_at: "2026-09-10T05:00:00Z" },
      { ...ROWS[2] }, // no date
    ];
    const publishedWindow = { since: "2026-09-24T09:00:00.000Z", until: "2026-10-01T09:00:00.000Z" };
    expect(ids(filterExports(rows, { publishedWindow }, ctx))).toEqual(["100021"]);
    expect(ids(filterExports(rows, { publishedWindow: null }, ctx))).toEqual(["100021", "100022", "100023"]);
  });
  it("filters by the newest run's status, including never ran", () => {
    expect(ids(filterExports(ROWS, { lastOff: ["failed"] }, ctx))).toEqual(["100022", "100023"]);
    expect(ids(filterExports(ROWS, { lastOff: [NEVER_RAN] }, ctx))).toEqual(["100021", "100022"]);
  });
});

describe("helpers", () => {
  it("reads the newest run, or none", () => {
    expect(lastRunKey("100021", RUNS)).toBe("failed");
    expect(lastRunKey("100099", RUNS)).toBe(NEVER_RAN);
  });
  it("keys the schedule", () => {
    expect(scheduleKey("100021", SCHEDULES)).toBe("scheduled");
    expect(scheduleKey("100022", SCHEDULES)).toBe("manual");
  });
  it("reads an entry's formats from the list, the single value or the rendering (UIFPE-749)", () => {
    expect(exportFormats({ settings: { formats: ["csv", "json"] } })).toEqual(["csv", "json"]);
    expect(exportFormats({ settings: { format: "CSV" } })).toEqual(["csv"]);
    expect(exportFormats({ settings: {}, export_config: { rendering: { format: "xml" } } })).toEqual(["xml"]);
    expect(exportFormats({ settings: {} })).toEqual([]);
    expect(exportFormats(null)).toEqual([]);
  });
  it("shortens a catalog path to its table name", () => {
    expect(shortTable("glue_catalog.uif_echo_dev_gold_product.product")).toBe("product");
    expect(shortTable("")).toBe("");
  });
});

describe("isNewExport (UIFPE-722)", () => {
  const NOW = Date.parse("2026-09-30T12:00:00Z");
  it("is a first version published in the last day", () => {
    expect(isNewExport({ version: 1, published_at: "2026-09-30T09:00:00Z" }, NOW)).toBe(true);
  });
  it("is not a later version, an older one, or one without a date", () => {
    expect(isNewExport({ version: 2, published_at: "2026-09-30T09:00:00Z" }, NOW)).toBe(false);
    expect(isNewExport({ version: 1, published_at: "2026-09-28T09:00:00Z" }, NOW)).toBe(false);
    expect(isNewExport({ version: 1 }, NOW)).toBe(false);
    expect(isNewExport(null, NOW)).toBe(false);
  });
});
