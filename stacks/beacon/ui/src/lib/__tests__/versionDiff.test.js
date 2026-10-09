import { describe, expect, it } from "vitest";
import { deliveryKeys, versionDiff } from "../versionDiff.js";

const src = { name: "product", path: "glue_catalog.uif_echo_dev_gold_product.product" };
const base = {
  settings: { format: "CSV", packaging: { zip: true }, destination_id: "d1", delivery: { channel: "sftp", host: "sftp.x", remote_dir: "/in" } },
  mapping_config: {
    sources: [src],
    joins: [],
    filters: ["product.item_type = 'BOOK'"],
    mappings: [
      { type: "one_to_one", source: "product.isbn13", target: "isbn13" },
      { type: "one_to_one", source: "product.description", target: "title" },
      { type: "one_to_one", source: "product.legacy", target: "legacy_code" },
    ],
  },
};
const names = { d1: "Ingram Content Group", d2: "Nielsen BookData" };
const nameOf = (id) => names[id] || null;

function edit(fn) {
  const v = JSON.parse(JSON.stringify(base));
  fn(v);
  return v;
}

describe("versionDiff (UIFPE-760)", () => {
  it("is empty for an unchanged republish, and for a missing side", () => {
    expect(versionDiff(base, edit(() => {}), { nameOf })).toEqual([]);
    expect(versionDiff(null, base)).toEqual([]);
  });

  it("finds an added, a removed and a changed field", () => {
    const next = edit((v) => {
      v.mapping_config.mappings = [
        { type: "one_to_one", source: "product.isbn13", target: "isbn13" },
        { type: "one_to_one", source: "product.long_description", target: "title" },
        { type: "derived", expression: "round(price.amount, 2)", target: "list_price" },
      ];
    });
    expect(versionDiff(base, next, { nameOf })).toEqual([
      { kind: "mod", area: "Mappings", subject: "title", detail: "now reads product.long_description" },
      { kind: "add", area: "Mappings", subject: "list_price", detail: "added · round(price.amount, 2)" },
      { kind: "del", area: "Mappings", subject: "legacy_code", detail: "removed" },
    ]);
  });

  it("finds a changed filter as one removed and one added condition", () => {
    const next = edit((v) => {
      v.mapping_config.filters = ["product.item_type = 'EBOOK'"];
    });
    const d = versionDiff(base, next, { nameOf });
    expect(d.map((x) => [x.kind, x.area, x.subject])).toEqual([
      ["add", "Rules", "Filter"],
      ["del", "Rules", "Filter"],
    ]);
    expect(d[0].detail).toContain("EBOOK");
  });

  it("finds a new join and an added destination, by name", () => {
    const next = edit((v) => {
      v.mapping_config.sources.push({ name: "price", path: "glue_catalog.uif_echo_dev_gold_price.price" });
      v.mapping_config.joins = [{ left: "product", right: "price", type: "left", condition: "product.product_id = price.product_id" }];
      v.settings.deliveries = [
        { channel: "sftp", host: "sftp.x", remote_dir: "/in", destination_id: "d1" },
        { channel: "sftp", host: "ftp.n", remote_dir: "/p", destination_id: "d2" },
      ];
    });
    const d = versionDiff(base, next, { nameOf });
    expect(d).toContainEqual({ kind: "add", area: "Rules", subject: "price", detail: "joined (left)" });
    expect(d).toContainEqual({ kind: "add", area: "Delivery", subject: "Nielsen BookData", detail: "now delivered to" });
    expect(d.filter((x) => x.area === "Delivery")).toHaveLength(1); // d1 is the same target
  });

  it("says what changed in the output", () => {
    const next = edit((v) => {
      v.settings.format = "JSON";
      v.settings.packaging = { zip: false, manifest: false };
      v.settings.extraction = { mode: "incremental" };
    });
    expect(versionDiff(base, next, { nameOf }).map((x) => `${x.subject}: ${x.detail}`)).toEqual([
      "Format: CSV → JSON",
      "Packaging: no longer a ZIP",
      "Manifest: no longer added",
      "Extraction: Full load → Incremental",
    ]);
  });

  it("names the managed drop when a version names no destination", () => {
    expect(deliveryKeys({})).toEqual([{ key: "managed", label: "Managed S3 partner-drop" }]);
  });
});
