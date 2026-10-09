import { describe, expect, it } from "vitest";
import { allSelected, filterItems, selectionLabel, toggleAll, toggleOne } from "../multiSelect.js";

const ITEMS = [
  { key: "product", label: "product" },
  { key: "price", label: "price" },
  { key: "program", label: "program" },
  { key: "stock", label: "stock" },
];

describe("selectionLabel", () => {
  it("shows the placeholder when nothing is picked", () => {
    expect(selectionLabel(ITEMS, [], "domains", "Choose domains")).toEqual({ text: "Choose domains", empty: true });
  });
  it("names a single pick", () => {
    expect(selectionLabel(ITEMS, ["price"], "domains").text).toBe("price");
  });
  it("counts several picks and says out of how many", () => {
    expect(selectionLabel(ITEMS, ["price", "stock"], "domains")).toEqual({ text: "2 domains selected", of: 4, empty: false });
  });
  it("says All when every option is picked", () => {
    expect(selectionLabel(ITEMS, ["stock", "product", "price", "program"], "domains").text).toBe("All domains (4)");
  });
  it("ignores keys that are not options", () => {
    expect(selectionLabel(ITEMS, ["gone"], "domains", "Choose").empty).toBe(true);
  });
});

describe("filterItems", () => {
  it("keeps every option for an empty search", () => {
    expect(filterItems(ITEMS, "  ")).toHaveLength(4);
  });
  it("matches part of a label, ignoring case", () => {
    expect(filterItems(ITEMS, "PR").map((i) => i.key)).toEqual(["product", "price", "program"]);
  });
  it("returns nothing when no label matches", () => {
    expect(filterItems(ITEMS, "zzz")).toEqual([]);
  });
});

describe("toggleAll", () => {
  it("selects every option when some are missing", () => {
    expect(toggleAll(ITEMS, ["price"]).sort()).toEqual(["price", "product", "program", "stock"]);
  });
  it("clears every option when all are picked", () => {
    expect(toggleAll(ITEMS, ["product", "price", "program", "stock"])).toEqual([]);
  });
  it("leaves keys outside the list alone", () => {
    expect(toggleAll(ITEMS, ["product", "price", "program", "stock", "other"])).toEqual(["other"]);
  });
  it("treats an empty list as nothing to select", () => {
    expect(allSelected([], [])).toBe(false);
  });
});

describe("toggleOne", () => {
  it("adds a missing key and removes a present one", () => {
    expect(toggleOne(["price"], "stock")).toEqual(["price", "stock"]);
    expect(toggleOne(["price", "stock"], "price")).toEqual(["stock"]);
  });
});
