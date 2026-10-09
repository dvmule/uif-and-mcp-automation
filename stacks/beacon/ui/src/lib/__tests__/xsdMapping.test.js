// UIFPE-616: rows held to an uploaded XSD, "Leave out" for optional elements,
// and the inline-edit rules of the Mapping table.
import { describe, expect, it } from "vitest";
import {
  canLeaveOut,
  canRemoveRow,
  editSource,
  isSchemaTarget,
  mappingFromText,
  mappingCountLine,
  mappingFilled,
  mappingText,
  renameError,
  schemaLocked,
  validLeftOut,
} from "../xsdMapping.js";

const GUIDE = {
  paths: [
    { path: "ISBN13", required: true },
    { path: "Title", required: true },
    { path: "Subtitle", required: false },
    { path: "Price@currency", required: false, kind: "attribute" },
  ],
  declared: ["ISBN13", "Title", "Subtitle", "Price", "Price@currency"],
};

describe("schemaLocked", () => {
  it("holds only an XML export with an uploaded schema that has paths", () => {
    expect(schemaLocked("xml", { id: "s1" }, GUIDE)).toBe(true);
    expect(schemaLocked("csv", { id: "s1" }, GUIDE)).toBe(false);
    expect(schemaLocked("xml", null, GUIDE)).toBe(false);
    expect(schemaLocked("xml", { id: "s1" }, { paths: [] })).toBe(false);
    expect(schemaLocked("xml", { id: "s1" }, null)).toBe(false); // guide still loading
  });
});

describe("isSchemaTarget / canRemoveRow", () => {
  it("knows the XSD's paths and declared elements", () => {
    expect(isSchemaTarget(GUIDE, "Subtitle")).toBe(true);
    expect(isSchemaTarget(GUIDE, "Price")).toBe(true); // declared, not a leaf path
    expect(isSchemaTarget(GUIDE, "LegacyCode")).toBe(false);
    expect(isSchemaTarget(GUIDE, "")).toBe(false);
  });

  it("XSD rows cannot be removed; rows it does not declare can", () => {
    expect(canRemoveRow(true, GUIDE, "Title")).toBe(false);
    expect(canRemoveRow(true, GUIDE, "LegacyCode")).toBe(true);
  });

  it("without an XSD every row can be removed", () => {
    expect(canRemoveRow(false, GUIDE, "Title")).toBe(true);
  });
});

describe("canLeaveOut / validLeftOut", () => {
  it("only optional XSD paths may be left out", () => {
    expect(canLeaveOut(GUIDE, "Subtitle")).toBe(true);
    expect(canLeaveOut(GUIDE, "Price@currency")).toBe(true);
    expect(canLeaveOut(GUIDE, "ISBN13")).toBe(false); // required
    expect(canLeaveOut(GUIDE, "LegacyCode")).toBe(false); // not in the XSD
    expect(canLeaveOut(null, "Subtitle")).toBe(false);
  });

  it("drops saved entries this XSD no longer allows, and duplicates", () => {
    expect(validLeftOut(GUIDE, ["Subtitle", "Title", "Gone", "Subtitle"])).toEqual(["Subtitle"]);
    expect(validLeftOut(GUIDE, undefined)).toEqual([]);
  });
});

describe("mappingText / mappingFromText", () => {
  it("shows what the row reads from", () => {
    expect(mappingText({ source: "product.title" })).toBe("product.title");
    expect(mappingText({ expression: "upper(product.title)" })).toBe("upper(product.title)");
    expect(mappingText({ type: "literal", value: 0 })).toBe("0");
    expect(mappingText(null)).toBe("");
  });

  it("alias.column is a column, anything else an expression", () => {
    expect(mappingFromText(" product.title ", "Title")).toEqual({ type: "one_to_one", source: "product.title", target: "Title" });
    expect(mappingFromText("concat(a.x, '-')", "T")).toEqual({ type: "derived", expression: "concat(a.x, '-')", target: "T" });
    expect(mappingFromText("  ", "T")).toBeNull();
  });
});

describe("editSource", () => {
  const built = { type: "derived", expression: "upper(product.title)", target: "Title" };

  it("unchanged text keeps the row — an fx-built or literal row stays itself", () => {
    expect(editSource(built, " upper(product.title) ")).toEqual({ mapping: built, changed: false });
    const lit = { type: "literal", value: "GBP", target: "Currency" };
    expect(editSource(lit, "GBP").mapping).toBe(lit);
  });

  it("new text becomes a plain column or expression row on the same target", () => {
    expect(editSource(built, "product.subtitle")).toEqual({
      mapping: { type: "one_to_one", source: "product.subtitle", target: "Title" },
      changed: true,
    });
  });

  it("blank is refused", () => {
    expect(editSource(built, "  ").error).toMatch(/required/);
  });
});

describe("renameError", () => {
  const rows = [
    { target: "isbn" },
    { target: "title" },
    { type: "one_to_many", targets: ["a", "b"] },
  ];

  it("allows a free name and keeping its own", () => {
    expect(renameError(rows, 0, "isbn13")).toBeNull();
    expect(renameError(rows, 0, " isbn ")).toBeNull();
  });

  it("refuses blank and a name another row writes", () => {
    expect(renameError(rows, 0, " ")).toMatch(/required/);
    expect(renameError(rows, 0, "title")).toMatch(/already/);
    expect(renameError(rows, 0, "b")).toMatch(/already/);
  });
});

describe("mappingFilled / mappingCountLine (UIFPE-718)", () => {
  const col = (target, source) => ({ type: "one_to_one", target, source });
  it("calls a row valid once it has a target and a source", () => {
    expect(mappingFilled(col("isbn13", "product.isbn13"))).toBe(true);
    expect(mappingFilled({ type: "derived", target: "title", expression: "upper(product.description)" })).toBe(true);
    expect(mappingFilled({ type: "literal", target: "channel", value: "UK" })).toBe(true);
    expect(mappingFilled(col("isbn13", ""))).toBe(false);
    expect(mappingFilled(col("", "product.isbn13"))).toBe(false);
  });
  it("sums the rows the way the prototype's count line does", () => {
    expect(mappingCountLine({ mappings: [col("a", "p.a"), col("b", "p.b")] })).toBe("2 fields · 2 valid");
    expect(mappingCountLine({ mappings: [col("a", "p.a"), col("b", "")], pending: 1, shape: "one row per price" })).toBe(
      "3 fields · 1 valid · 2 to fill in · one row per price"
    );
    expect(mappingCountLine({ mappings: [col("a", "p.a")], unresolved: 1 })).toBe("1 field · 1 valid · 1 unresolved");
    expect(mappingCountLine({ mappings: [] })).toBe("0 fields · 0 valid");
  });
});
