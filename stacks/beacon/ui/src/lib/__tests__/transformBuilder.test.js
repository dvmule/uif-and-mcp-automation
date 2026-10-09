// UIFPE-425: every guided transformation compiles to the exact expression we
// expect — these strings are the contract with the engine's allowlist, so a
// change here means re-checking ALLOWED_FUNCTIONS in config_validator.py.
import { describe, expect, it } from "vitest";
import {
  TRANSFORMS,
  TRANSFORM_GROUPS,
  buildTransform,
  previewTransform,
  sqlString,
  suggestTarget,
  transformById,
} from "../transformBuilder.js";

describe("sqlString", () => {
  it("quotes plainly", () => expect(sqlString("yes")).toBe("'yes'"));
  it("doubles single quotes", () => expect(sqlString("it's")).toBe("'it''s'"));
  it("stringifies non-strings", () => expect(sqlString(5)).toBe("'5'"));
});

describe("buildTransform — each menu entry", () => {
  it("if_null (motivating sample 1)", () => {
    const r = buildTransform(
      "if_null",
      { column: "product.ppid", whenPresent: "Available", whenMissing: "No" },
      "ppidFlag"
    );
    expect(r.ok).toBe(true);
    expect(r.row).toEqual({
      type: "derived",
      target: "ppidFlag",
      expression: "CASE WHEN product.ppid IS NOT NULL THEN 'Available' ELSE 'No' END",
    });
  });

  it("if_null with an empty not-null text keeps the column's own value", () => {
    const r = buildTransform(
      "if_null",
      { column: "product.ppid", whenPresent: "", whenMissing: "Not available" },
      "ppid"
    );
    expect(r.row.expression).toBe(
      "CASE WHEN product.ppid IS NOT NULL THEN product.ppid ELSE 'Not available' END"
    );
  });

  it("if_null with an empty null text stays null", () => {
    const r = buildTransform(
      "if_null",
      { column: "product.ppid", whenPresent: "Available", whenMissing: "" },
      "ppidFlag"
    );
    expect(r.row.expression).toBe(
      "CASE WHEN product.ppid IS NOT NULL THEN 'Available' ELSE NULL END"
    );
  });

  it("if_null refuses both texts empty (a no-op)", () => {
    const r = buildTransform(
      "if_null",
      { column: "product.ppid", whenPresent: "", whenMissing: "" },
      "ppidFlag"
    );
    expect(r.ok).toBe(false);
  });

  it("date_format with a stored-as format (motivating sample 2)", () => {
    const r = buildTransform(
      "date_format",
      { column: "product.pubDate", fromFormat: "yyyy-MM-dd", toFormat: "dd/MM/yyyy" },
      "pubDate"
    );
    expect(r.row.expression).toBe(
      "date_format(to_date(product.pubDate, 'yyyy-MM-dd'), 'dd/MM/yyyy')"
    );
  });

  it("date_format without a stored-as format (already a date)", () => {
    const r = buildTransform(
      "date_format",
      { column: "product.updatedAt", fromFormat: "", toFormat: "dd-MM-yyyy" },
      "updated"
    );
    expect(r.row.expression).toBe("date_format(product.updatedAt, 'dd-MM-yyyy')");
  });

  it("map_values with a fallback", () => {
    const r = buildTransform(
      "map_values",
      {
        column: "product.itemType",
        pairs: [
          { from: "P", to: "Print" },
          { from: "D", to: "Digital" },
        ],
        fallback: "Other",
      },
      "format"
    );
    expect(r.row.expression).toBe(
      "CASE WHEN product.itemType = 'P' THEN 'Print' WHEN product.itemType = 'D' THEN 'Digital' ELSE 'Other' END"
    );
  });

  it("map_values without a fallback keeps the original value", () => {
    const r = buildTransform(
      "map_values",
      { column: "product.itemType", pairs: [{ from: "P", to: "Print" }], fallback: "" },
      "format"
    );
    expect(r.row.expression).toBe(
      "CASE WHEN product.itemType = 'P' THEN 'Print' ELSE product.itemType END"
    );
  });

  it("default_when_empty", () => {
    const r = buildTransform(
      "default_when_empty",
      { column: "product.language", defaultValue: "en" },
      "language"
    );
    expect(r.row.expression).toBe("coalesce(product.language, 'en')");
  });

  it("combine", () => {
    const r = buildTransform(
      "combine",
      { columns: ["product.disciplineLevel1", "product.disciplineLevel2"], separator: " / " },
      "discipline"
    );
    expect(r.row.expression).toBe(
      "concat_ws(' / ', product.disciplineLevel1, product.disciplineLevel2)"
    );
  });

  it("combine allows an empty separator (join with nothing)", () => {
    const r = buildTransform(
      "combine",
      { columns: ["product.isbn13", "product.editionnumber"] },
      "code"
    );
    expect(r.row.expression).toBe("concat_ws('', product.isbn13, product.editionnumber)");
  });

  it("combine keeps a plain-space separator", () => {
    const r = buildTransform(
      "combine",
      { columns: ["product.brand", "product.subbrand"], separator: " " },
      "brandFull"
    );
    expect(r.row.expression).toBe("concat_ws(' ', product.brand, product.subbrand)");
  });

  it("split_part", () => {
    const r = buildTransform(
      "split_part",
      { column: "product.isbn13", separator: "-", part: "2" },
      "isbnGroup"
    );
    expect(r.row.expression).toBe("split_part(product.isbn13, '-', 2)");
  });

  it("split_part accepts a space separator but not an empty one", () => {
    expect(
      buildTransform("split_part", { column: "product.description", separator: " ", part: "1" }, "w")
        .row.expression
    ).toBe("split_part(product.description, ' ', 1)");
    expect(
      buildTransform("split_part", { column: "product.description", separator: "", part: "1" }, "w").ok
    ).toBe(false);
  });

  it("change_case with trim", () => {
    const r = buildTransform(
      "change_case",
      { column: "product.title", mode: "upper", trim: "yes" },
      "title"
    );
    expect(r.row.expression).toBe("upper(trim(product.title))");
  });

  it("change_case keep-case + trim only", () => {
    const r = buildTransform(
      "change_case",
      { column: "product.title", mode: "none", trim: "yes" },
      "title"
    );
    expect(r.row.expression).toBe("trim(product.title)");
  });

  it("change_case refuses a no-op", () => {
    const r = buildTransform(
      "change_case",
      { column: "product.title", mode: "none", trim: "no" },
      "title"
    );
    expect(r.ok).toBe(false);
  });

  it("find_replace plain and regex", () => {
    expect(
      buildTransform(
        "find_replace",
        { column: "product.title", find: "&", replaceWith: "and", regex: "plain" },
        "title"
      ).row.expression
    ).toBe("replace(product.title, '&', 'and')");
    expect(
      buildTransform(
        "find_replace",
        { column: "product.title", find: "\\s+", replaceWith: " ", regex: "regex" },
        "title"
      ).row.expression
    ).toBe("regexp_replace(product.title, '\\s+', ' ')");
  });

  it("find_replace allows replacing with nothing", () => {
    const r = buildTransform(
      "find_replace",
      { column: "product.title", find: "™", replaceWith: "", regex: "plain" },
      "title"
    );
    expect(r.ok).toBe(true);
    expect(r.row.expression).toBe("replace(product.title, '™', '')");
  });

  it("substring", () => {
    const r = buildTransform(
      "substring",
      { column: "product.isbn13", start: "1", length: "3" },
      "prefix"
    );
    expect(r.row.expression).toBe("substring(product.isbn13, 1, 3)");
  });

  it("pad_to_width left and right", () => {
    expect(
      buildTransform(
        "pad_to_width",
        { column: "product.sku", width: "10", padChar: "0", side: "left" },
        "sku"
      ).row.expression
    ).toBe("lpad(product.sku, 10, '0')");
    expect(
      buildTransform(
        "pad_to_width",
        { column: "product.sku", width: "10", padChar: " ", side: "right" },
        "sku"
      ).row.expression
    ).toBe("rpad(product.sku, 10, ' ')");
  });

  it("round_number in both modes", () => {
    expect(
      buildTransform("round_number", { column: "price.amount", decimals: "2", mode: "round" }, "amount")
        .row.expression
    ).toBe("round(price.amount, 2)");
    expect(
      buildTransform("round_number", { column: "price.amount", decimals: "0", mode: "format" }, "amount")
        .row.expression
    ).toBe("format_number(price.amount, 0)");
  });

  it("mask_hash casts to string for sha2", () => {
    const r = buildTransform("mask_hash", { column: "product.ppid" }, "ppidHash");
    expect(r.row.expression).toBe("sha2(cast(product.ppid as string), 256)");
  });

  it("fixed_value emits a literal rule, not an expression", () => {
    const r = buildTransform("fixed_value", { value: "gold-product" }, "sourceSystem");
    expect(r.row).toEqual({ type: "literal", target: "sourceSystem", value: "gold-product" });
  });
});

describe("buildTransform — guardrails", () => {
  it("rejects an unknown id", () => {
    expect(buildTransform("nope", {}, "t").ok).toBe(false);
  });
  it("requires a target", () => {
    expect(buildTransform("mask_hash", { column: "a.b" }, "  ").ok).toBe(false);
  });
  it("reports the missing input by its label", () => {
    const r = buildTransform("default_when_empty", { column: "a.b" }, "t");
    expect(r.ok).toBe(false);
    expect(r.error).toContain("Default value");
  });
  it("rejects non-numeric and below-min numbers", () => {
    expect(
      buildTransform("split_part", { column: "a.b", separator: "-", part: "x" }, "t").ok
    ).toBe(false);
    expect(
      buildTransform("split_part", { column: "a.b", separator: "-", part: "0" }, "t").ok
    ).toBe(false);
  });
  it("combine needs two columns", () => {
    expect(buildTransform("combine", { columns: ["a.b"], separator: "," }, "t").ok).toBe(false);
  });
  it("map_values needs at least one filled pair", () => {
    expect(
      buildTransform("map_values", { column: "a.b", pairs: [{ from: "", to: "x" }] }, "t").ok
    ).toBe(false);
  });
  it("escapes quotes end to end (no SQL breakage from author text)", () => {
    const r = buildTransform(
      "if_null",
      { column: "a.b", whenPresent: "it's here", whenMissing: "n/a" },
      "t"
    );
    expect(r.row.expression).toBe(
      "CASE WHEN a.b IS NOT NULL THEN 'it''s here' ELSE 'n/a' END"
    );
  });
});

describe("previewTransform / suggestTarget / catalog", () => {
  it("preview is null while incomplete, the expression when complete", () => {
    expect(previewTransform("mask_hash", {})).toBeNull();
    expect(previewTransform("mask_hash", { column: "a.b" })).toBe(
      "sha2(cast(a.b as string), 256)"
    );
  });
  it("preview marks a literal", () => {
    expect(previewTransform("fixed_value", { value: "x" })).toBe("(fixed value) 'x'");
  });
  it("suggestTarget uses the column's own name", () => {
    expect(suggestTarget({ column: "product.ppid" })).toBe("ppid");
    expect(suggestTarget({ columns: ["price.amount", "price.currency"] })).toBe("amount");
    expect(suggestTarget({})).toBe("");
  });
  it("every entry has an id, label, inputs, and build", () => {
    for (const t of TRANSFORMS) {
      expect(t.id).toBeTruthy();
      expect(t.label).toBeTruthy();
      expect(Array.isArray(t.inputs)).toBe(true);
      expect(typeof t.build).toBe("function");
      expect(transformById(t.id)).toBe(t);
    }
  });

  it("every entry carries plain-English info and belongs to a known group (v2 popup)", () => {
    for (const t of TRANSFORMS) {
      expect(TRANSFORM_GROUPS).toContain(t.group);
      expect(t.info?.what?.length).toBeGreaterThan(20);
      expect(Array.isArray(t.info.example)).toBe(true);
      expect(t.info.example.length).toBeGreaterThan(0);
      for (const pair of t.info.example) expect(pair).toHaveLength(2);
    }
  });
});
