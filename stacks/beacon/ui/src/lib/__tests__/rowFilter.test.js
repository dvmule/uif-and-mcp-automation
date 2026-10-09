// rowFilter.test.js — the wizard's row-filter model (the structured Filter
// step), pinned without a browser. These rules decide the exact Spark SQL
// the engine runs as `filters`, so escaping, operator rendering and the
// identifier clean-up are asserted character by character. Two regressions
// are pinned by name: the edit prefill that kept only filters[0] (a
// republish silently narrowed scope), and the doubled-quote escape that
// Spark reads as two adjacent literals.
import { describe, expect, it } from "vitest";

import {
  FILTER_BYTES_CAP,
  FILTER_CHECK_STUB_MAPPING,
  IDENTIFIER_COLUMN_RE,
  LIST_CAP,
  TEXT_CARDINALITY_CAP,
  arrayElementCategory,
  attributeClauseForColumn,
  attributeColumnsFor,
  canDownload,
  cardinalityLabel,
  clauseIssues,
  describeHidden,
  describeMeasuring,
  columnGlueType,
  compileClause,
  compileFilters,
  completeClauses,
  customClause,
  describeClause,
  describeDownload,
  describeFilters,
  describePreviewCounts,
  detectIdentifierColumns,
  downloadFor,
  emptyClause,
  escapeLiteral,
  filterCheckConfig,
  filterCheckIssues,
  fmtBytes,
  listIndexOf,
  missingFor,
  previewRequest,
  previewSignature,
  suggestionsEligible,
  filterContext,
  filterIssues,
  filterRuleDefinition,
  filterRuleLabel,
  filterSetIssues,
  filtersComplete,
  filtersFromConfig,
  glueTypeCategory,
  isComplete,
  isbn13CheckDigitOk,
  leftJoinHint,
  likeLiteral,
  normalizeIdentifiers,
  operatorsFor,
  renameStaleReferences,
  renderLiteral,
  suggestListColumn,
  unknownQualifiers,
  unqualifiedColumns,
  upgradeClauseCategories,
} from "../rowFilter.js";

// catalog table records as the picker emits them (no `columns` list)
const tbl = (domain, table) => ({
  id: `${domain}.${table}`,
  path: `glue_catalog.uif_echo_dev_gold_${domain}.${table}`,
  kind: "iceberg",
  domain,
});
const PRODUCT = tbl("product", "product");
const PRICE = tbl("price", "price");
const STOCK = tbl("stock", "stock");

// the columns route's result: names always, `types` only for live iceberg
const live = (columns, types) => ({ columns, provenance: "live", ...(types ? { types } : {}) });
const ATTRS = {
  [PRODUCT.id]: live(["ppid", "isbn13", "title", "price", "pubDate", "ts", "flag", "programPpid", "spec"], {
    ppid: "string",
    isbn13: "string",
    title: "string",
    price: "decimal(10,2)",
    pubDate: "date",
    ts: "timestamp",
    flag: "boolean",
    programPpid: "array<string>",
    spec: "struct<a:int>",
  }),
  [PRICE.id]: live(["ppid", "vatCountry", "priceSpecifications"]), // no type map
  [STOCK.id]: live(["ppid", "organizationCode", "qtyOnHand"], {
    ppid: "string",
    organizationCode: "string",
    qtyOnHand: "bigint",
  }),
};

const entry = (source, alias, join = null) => ({ source, alias, join });
const pairJoin = (leftRef, rightColumn, type = "left") => ({ type, pairs: [{ leftRef, rightColumn }], condition: null });
const FEED = [
  entry(PRODUCT, "product"),
  entry(PRICE, "price", pairJoin("product.ppid", "ppid")),
  entry(STOCK, "stock", pairJoin("product.ppid", "ppid", "inner")),
];
const CTX = filterContext(FEED, ATTRS);

// The Gold tables as they are actually spelled (snake_case, product_id IS the
// PPID — no table has a ppid column), with the types the columns route
// returns for live iceberg. Same catalog ids as above, a separate attrs map.
const PROGRAM = tbl("program", "program");
const GOLD = {
  [PRODUCT.id]: live(
    [
      "inventory_item_id",
      "organization_id",
      "product_id",
      "isbn13",
      "isbn10",
      "source_item_number",
      "description",
      "long_description",
      "item_type",
      "brand",
      "list_price",
      "publication_date",
      "is_digital",
      "cover_image_large_url",
      "associated_programs",
    ],
    {
      inventory_item_id: "bigint",
      organization_id: "bigint",
      product_id: "string",
      isbn13: "string",
      isbn10: "string",
      source_item_number: "string",
      description: "string",
      long_description: "string",
      item_type: "string",
      brand: "string",
      list_price: "decimal(12,2)",
      publication_date: "date",
      is_digital: "boolean",
      cover_image_large_url: "string",
      associated_programs: "array<string>",
    }
  ),
  [PRICE.id]: live(["product_id", "isbn13", "vat_country", "price_specifications"], {
    product_id: "string",
    isbn13: "string",
    vat_country: "string",
    price_specifications: "array<struct<currency:string,amount:decimal(12,2)>>",
  }),
  [STOCK.id]: live(["product_id", "organization_code", "on_hand_quantity"], {
    product_id: "string",
    organization_code: "string",
    on_hand_quantity: "bigint",
  }),
  [PROGRAM.id]: live(["program_id", "description", "program_family_catalog_code", "inventory_item_status"], {
    program_id: "string",
    description: "string",
    program_family_catalog_code: "string",
    inventory_item_status: "string",
  }),
};
const GOLD_FEED = [
  entry(PRODUCT, "product"),
  entry(PRICE, "price", pairJoin("product.product_id", "product_id")),
  entry(STOCK, "stock", pairJoin("product.product_id", "product_id", "inner")),
  entry(PROGRAM, "program", pairJoin("product.product_id", "program_id")),
];
const GOLD_CTX = filterContext(GOLD_FEED, GOLD);

// A finished profile as the wiring agent stores it: an item × organisation
// grain (2.1 M rows), so the unique_like flag is true for far more than the
// identifiers — which is exactly why no rule reads it any more.
const stat = (distinct, over = {}) => ({ distinct, unique_like: distinct >= 1_050_000, identifier: false, ...over });
const PRODUCT_PROFILE = {
  status: "done",
  rows: 2_100_000,
  columns: {
    inventory_item_id: stat(2_100_000, { identifier: true }),
    organization_id: stat(12),
    product_id: stat(1_900_000, { identifier: true }),
    isbn13: stat(1_850_000, { identifier: true }),
    isbn10: stat(1_200_000, { identifier: true }),
    source_item_number: stat(1_900_000),
    description: stat(2_100_000),
    long_description: stat(2_100_000),
    item_type: stat(5),
    brand: stat(300),
    list_price: stat(48_000),
    publication_date: stat(9_000),
    is_digital: stat(2),
    cover_image_large_url: stat(2_100_000),
    // associated_programs is an array: not scalar, not profiled
  },
};

const list = (values, over = {}) => ({
  id: "l1",
  kind: "list",
  column: "product.ppid",
  idKind: "ppid",
  values,
  invalid: [],
  suspicious: [],
  ...over,
});
const attr = (over = {}) => ({
  id: "a1",
  kind: "attribute",
  column: "product.title",
  category: "string",
  op: "eq",
  value: "BOOK",
  value2: "",
  ...over,
});
const custom = (sql) => ({ id: "x1", kind: "custom", sql });

const blocks = (issues) => issues.filter((i) => i.level === "block").map((i) => i.message);
const warns = (issues) => issues.filter((i) => i.level === "warn").map((i) => i.message);

describe("escapeLiteral", () => {
  it("wraps in single quotes", () => {
    expect(escapeLiteral("BOOK")).toBe("'BOOK'");
    expect(escapeLiteral("")).toBe("''");
  });
  it("escapes an apostrophe with a backslash (Spark reads '' as two literals)", () => {
    expect(escapeLiteral("O'Brien")).toBe("'O\\'Brien'");
  });
  it("escapes backslashes first, so a backslash-quote pair stays two characters", () => {
    expect(escapeLiteral("a\\b")).toBe("'a\\\\b'");
    expect(escapeLiteral("\\'")).toBe("'\\\\\\''");
  });
});

describe("likeLiteral", () => {
  it("escapes LIKE metacharacters so the user's text is matched literally", () => {
    expect(likeLiteral("50%", { prefix: "%", suffix: "%" })).toBe("'%50\\\\%%'");
    expect(likeLiteral("a_b", { prefix: "%", suffix: "%" })).toBe("'%a\\\\_b%'");
  });
  it("wraps with only the requested wildcards", () => {
    expect(likeLiteral("x", { suffix: "%" })).toBe("'x%'");
    expect(likeLiteral("x", { prefix: "%" })).toBe("'%x'");
  });
  it("still escapes quotes inside the pattern", () => {
    expect(likeLiteral("it's", { suffix: "%" })).toBe("'it\\'s%'");
  });
});

describe("renderLiteral", () => {
  it("accepts numbers and rejects anything else for the number category", () => {
    expect(renderLiteral("number", "42")).toBe("42");
    expect(renderLiteral("number", "-3.5")).toBe("-3.5");
    expect(renderLiteral("number", "4 2")).toBeNull();
    expect(renderLiteral("number", "abc")).toBeNull();
  });
  it("renders dates and timestamps through allowlisted functions", () => {
    expect(renderLiteral("date", "2026-01-01")).toBe("to_date('2026-01-01')");
    expect(renderLiteral("date", "01/01/2026")).toBeNull();
    expect(renderLiteral("timestamp", "2026-01-01T09:30")).toBe("to_timestamp('2026-01-01 09:30:00')");
    expect(renderLiteral("timestamp", "2026-01-01 09:30:15")).toBe("to_timestamp('2026-01-01 09:30:15')");
    expect(renderLiteral("timestamp", "2026-01-01")).toBe("to_timestamp('2026-01-01 00:00:00')");
    expect(renderLiteral("timestamp", "yesterday")).toBeNull();
  });
  it("trims and quotes strings; an empty value is not a literal", () => {
    expect(renderLiteral("string", " BOOK ")).toBe("'BOOK'");
    expect(renderLiteral("unknown", "x")).toBe("'x'");
    expect(renderLiteral("string", "   ")).toBeNull();
  });
});

describe("glueTypeCategory", () => {
  it.each([
    ["string", "string"],
    ["varchar(10)", "string"],
    ["char(2)", "string"],
    ["int", "number"],
    ["bigint", "number"],
    ["decimal(10,2)", "number"],
    ["double", "number"],
    ["boolean", "boolean"],
    ["date", "date"],
    ["timestamp", "timestamp"],
    ["timestamp_ntz", "timestamp"],
    ["array<string>", "array"],
    ["array<struct<a:int>>", "array"],
    ["struct<a:int>", "struct"],
    ["map<string,string>", "struct"],
    ["binary", "unknown"],
    ["", "unknown"],
    [undefined, "unknown"],
  ])("maps %s to %s", (glueType, category) => {
    expect(glueTypeCategory(glueType)).toBe(category);
  });
  it("reads the element category of an array type", () => {
    expect(arrayElementCategory("array<string>")).toBe("string");
    expect(arrayElementCategory("array<struct<a:int>>")).toBe("struct");
    expect(arrayElementCategory("string")).toBe("unknown");
  });
});

describe("operatorsFor", () => {
  const ids = (category, t) => operatorsFor(category, t).map((o) => o.op);
  it("gives each category its own operator family", () => {
    expect(ids("string")).not.toContain("gt");
    expect(ids("string")).toContain("contains");
    expect(ids("number")).toContain("between");
    expect(ids("boolean")).toEqual(["is_true", "is_false", "is_null", "not_null"]);
    expect(ids("struct")).toEqual(["is_null", "not_null"]);
    expect(ids("unknown")).toEqual(ids("string"));
  });
  it("offers array_contains only for string elements", () => {
    expect(ids("array", "array<string>")).toContain("has_value");
    expect(ids("array", "array<struct<a:int>>")).not.toContain("has_value");
    expect(ids("array", null)).toContain("has_value");
  });
  it("describes every operator with a label, arity and input type", () => {
    for (const category of ["string", "number", "date", "timestamp", "boolean", "array", "struct"]) {
      for (const o of operatorsFor(category)) {
        expect(o.label).toBeTruthy();
        expect([0, 1, 2]).toContain(o.arity);
        expect(o.arity === 0 ? o.input === "none" : o.input !== "none").toBe(true);
      }
    }
    expect(operatorsFor("date").find((o) => o.op === "on").input).toBe("date");
    expect(operatorsFor("timestamp").find((o) => o.op === "between").input).toBe("datetime-local");
    expect(operatorsFor("number").find((o) => o.op === "gt").input).toBe("number");
  });
});

describe("compileClause", () => {
  it("compiles an identifier list to an alias-qualified IN list", () => {
    expect(compileClause(list(["A1", "A2"]))).toBe("product.ppid IN ('A1', 'A2')");
    expect(compileClause(list([]))).toBe("product.ppid IN ({values})");
  });
  it("elides a long list for the on-screen preview only", () => {
    const c = list(["A1", "A2", "A3", "A4", "A5"]);
    expect(compileClause(c, { maxListPreview: 2 })).toBe("product.ppid IN ('A1', 'A2', … /* +3 more */)");
    expect(compileClause(c)).toBe("product.ppid IN ('A1', 'A2', 'A3', 'A4', 'A5')");
  });
  it.each([
    [attr(), "product.title = 'BOOK'"],
    [attr({ op: "neq" }), "product.title <> 'BOOK'"],
    [attr({ op: "contains", value: "50%" }), "product.title LIKE '%50\\\\%%'"],
    [attr({ op: "starts_with", value: "Intro" }), "product.title LIKE 'Intro%'"],
    [attr({ op: "ends_with", value: "ed." }), "product.title LIKE '%ed.'"],
    [attr({ column: "stock.qtyOnHand", category: "number", op: "gt", value: "0" }), "stock.qtyOnHand > 0"],
    [attr({ column: "stock.qtyOnHand", category: "number", op: "lte", value: "10" }), "stock.qtyOnHand <= 10"],
    [attr({ column: "product.price", category: "number", op: "between", value: "1", value2: "2" }), "product.price BETWEEN 1 AND 2"],
    [attr({ column: "product.pubDate", category: "date", op: "on", value: "2024-01-01" }), "product.pubDate = to_date('2024-01-01')"],
    [attr({ column: "product.ts", category: "timestamp", op: "on", value: "2024-01-01" }), "to_date(product.ts) = to_date('2024-01-01')"],
    [attr({ column: "product.pubDate", category: "date", op: "on_or_after", value: "2024-01-01" }), "product.pubDate >= to_date('2024-01-01')"],
    [attr({ column: "product.pubDate", category: "date", op: "before", value: "2024-01-01" }), "product.pubDate < to_date('2024-01-01')"],
    [attr({ column: "product.ts", category: "timestamp", op: "after", value: "2024-01-01T09:30" }), "product.ts > to_timestamp('2024-01-01 09:30:00')"],
    [attr({ column: "product.flag", category: "boolean", op: "is_true", value: "" }), "product.flag = true"],
    [attr({ column: "product.flag", category: "boolean", op: "is_false", value: "" }), "product.flag = false"],
    [attr({ column: "product.programPpid", category: "array", op: "has_value", value: "A1" }), "array_contains(product.programPpid, 'A1')"],
    [attr({ column: "product.programPpid", category: "array", op: "is_empty", value: "" }), "(product.programPpid IS NULL OR size(product.programPpid) = 0)"],
    [attr({ column: "product.programPpid", category: "array", op: "not_empty", value: "" }), "size(product.programPpid) > 0"],
    [attr({ op: "is_null", value: "" }), "product.title IS NULL"],
    [attr({ op: "not_null", value: "" }), "product.title IS NOT NULL"],
  ])("renders %o", (clause, sql) => {
    expect(compileClause(clause)).toBe(sql);
  });
  it("never throws on a half-built clause — placeholders keep the preview alive", () => {
    expect(compileClause(attr({ value: "" }))).toBe("product.title = {value}");
    expect(compileClause(attr({ column: "" }))).toBe("{column} = 'BOOK'");
    expect(compileClause(attr({ op: "contains", value: "" }))).toBe("product.title LIKE {value}");
    expect(compileClause(attr({ column: "product.price", category: "number", op: "between", value: "1", value2: "" }))).toBe(
      "product.price BETWEEN 1 AND {value}"
    );
  });
  it("passes custom SQL through, trimmed", () => {
    expect(compileClause(custom("  size(price.priceSpecifications) > 0 "))).toBe("size(price.priceSpecifications) > 0");
  });
});

describe("isComplete / compileFilters", () => {
  it("knows what a finished clause is", () => {
    expect(isComplete(list(["A1"]))).toBe(true);
    expect(isComplete(list([]))).toBe(false);
    expect(isComplete(list(Array.from({ length: LIST_CAP + 1 }, (_, i) => `A${i}`)))).toBe(false);
    expect(isComplete(attr())).toBe(true);
    expect(isComplete(attr({ value: "" }))).toBe(false);
    expect(isComplete(attr({ column: "" }))).toBe(false);
    expect(isComplete(attr({ column: "product.price", category: "number", op: "gt", value: "abc" }))).toBe(false);
    expect(isComplete(attr({ column: "product.price", category: "number", op: "between", value: "1", value2: "" }))).toBe(false);
    expect(isComplete(attr({ op: "is_null", value: "" }))).toBe(true);
    expect(isComplete(attr({ op: "gt" }))).toBe(false); // gt is not a string operator
    expect(isComplete(custom("   "))).toBe(false);
    expect(isComplete(custom("a = 1"))).toBe(true);
  });
  it("compiles the complete clauses in order and skips the rest", () => {
    const clauses = [list(["A1"]), attr({ value: "" }), custom("a = 1")];
    expect(completeClauses(clauses).map((c) => c.id)).toEqual(["l1", "x1"]);
    expect(compileFilters(clauses)).toEqual(["product.ppid IN ('A1')", "a = 1"]);
  });
});

describe("normalizeIdentifiers", () => {
  it("splits on commas, semicolons and any whitespace and strips quotes and a BOM", () => {
    const r = normalizeIdentifiers("﻿'A1', \"A2\";A3\tA4\r\nA5", "ppid");
    expect(r.values).toEqual(["A1", "A2", "A3", "A4", "A5"]);
    expect(r.invalid).toEqual([]);
    expect(r.headerSkipped).toBe(false);
  });
  it("skips a header row for the known kinds only", () => {
    expect(normalizeIdentifiers("ppid\nA1\nA2", "ppid")).toMatchObject({ values: ["A1", "A2"], headerSkipped: true });
    expect(normalizeIdentifiers("ISBN-13\n9780134685991", "isbn13")).toMatchObject({
      values: ["9780134685991"],
      headerSkipped: true,
    });
    expect(normalizeIdentifiers("id\nx", "other")).toMatchObject({ values: ["id", "x"], headerSkipped: false });
  });
  it("skips the Gold spellings of an identifier header — product_id, inventory_item_id (UIFPE-514)", () => {
    expect(normalizeIdentifiers("product_id\nA1\nA2", "ppid")).toMatchObject({ values: ["A1", "A2"], headerSkipped: true });
    expect(normalizeIdentifiers("PRODUCT_ID,A1", "ppid")).toMatchObject({ values: ["A1"], headerSkipped: true });
    expect(normalizeIdentifiers("inventory_item_id\nA1", "ppid")).toMatchObject({ values: ["A1"], headerSkipped: true });
    // "other" never skips: a header word could be a legitimate value there
    expect(normalizeIdentifiers("product_id\nx", "other")).toMatchObject({ values: ["product_id", "x"], headerSkipped: false });
  });
  it("dedupes exactly and case-sensitively, first occurrence winning", () => {
    const r = normalizeIdentifiers("A1 A1 A2 a1", "ppid");
    expect(r.values).toEqual(["A1", "A2", "a1"]);
    expect(r.duplicates).toBe(1);
    expect(r.suspicious).toEqual([{ value: "a1", reason: "does not start with 'A'" }]);
  });
  it("validates ISBN-13s: canonical form, prefix, length and check digit", () => {
    const r = normalizeIdentifiers(
      ["9780134685991", "978-0-13-468599-1", "978-0262035613", "9780134685990", "1234567890128", "978013468599", "0134685997"].join(
        "\n"
      ),
      "isbn13"
    );
    expect(r.values).toEqual(["9780134685991", "9780262035613"]);
    expect(r.duplicates).toBe(1); // the hyphenated copy of the first
    expect(r.invalid).toEqual([
      { value: "9780134685990", reason: "check digit mismatch" },
      { value: "1234567890128", reason: "must start 978 or 979" },
      { value: "978013468599", reason: "not 13 digits" },
      { value: "0134685997", reason: "looks like an ISBN-10 — convert it to ISBN-13" },
    ]);
  });
  it("warns about a PPID not starting with A but rejects only SQL-hostile characters", () => {
    const r = normalizeIdentifiers("A123 B123 A'1 A(2)", "ppid");
    expect(r.values).toEqual(["A123", "B123"]);
    expect(r.suspicious).toEqual([{ value: "B123", reason: "does not start with 'A'" }]);
    expect(r.invalid.map((i) => i.value)).toEqual(["A'1", "A(2)"]);
  });
  it("accepts anything non-empty for other values", () => {
    expect(normalizeIdentifiers("x, y's, 12", "other").values).toEqual(["x", "y's", "12"]);
  });
  it("computes the ISBN-13 check digit", () => {
    expect(isbn13CheckDigitOk("9780134685991")).toBe(true);
    expect(isbn13CheckDigitOk("9780262035613")).toBe(true);
    expect(isbn13CheckDigitOk("9780134685990")).toBe(false);
    expect(isbn13CheckDigitOk("978013468599")).toBe(false);
  });
});

describe("detectIdentifierColumns / suggestListColumn / emptyClause", () => {
  it("finds identifier-shaped columns across the feed, driver first, names as listed", () => {
    const detected = detectIdentifierColumns(FEED, ATTRS);
    expect(detected.map((d) => `${d.alias}.${d.column}:${d.idKind}`)).toEqual([
      "product.ppid:ppid",
      "product.isbn13:isbn13",
      "price.ppid:ppid",
      "stock.ppid:ppid",
    ]);
    const upper = detectIdentifierColumns([entry(PRODUCT, "p")], { [PRODUCT.id]: live(["ISBN13", "Isbn_10"]) });
    expect(upper).toEqual([
      { alias: "p", column: "ISBN13", idKind: "isbn13", label: "ISBN-13" },
      { alias: "p", column: "Isbn_10", idKind: "other", label: "ISBN-10" },
    ]);
  });
  it("knows product_id is the PPID in Gold — and parent_product_id is not (UIFPE-514)", () => {
    const detected = detectIdentifierColumns(GOLD_FEED, GOLD);
    expect(detected.map((d) => `${d.alias}.${d.column}:${d.idKind}`)).toEqual([
      "product.product_id:ppid",
      "product.isbn13:isbn13",
      "product.isbn10:other",
      "price.product_id:ppid",
      "price.isbn13:isbn13",
      "stock.product_id:ppid",
    ]);
    expect(detected.find((d) => d.column === "product_id").label).toBe("PPID");
    expect(suggestListColumn("ppid", detected, "product")).toBe("product.product_id");
    const c = emptyClause("list", { detected, primaryAlias: "product" });
    expect(c).toMatchObject({ kind: "list", idKind: "ppid", column: "product.product_id" });
    const parent = detectIdentifierColumns([entry(PRODUCT, "p")], { [PRODUCT.id]: live(["parent_product_id", "Product_Id"]) });
    expect(parent).toEqual([{ alias: "p", column: "Product_Id", idKind: "ppid", label: "PPID" }]);
  });
  it("suggests the driving source's column, else the first match, else nothing", () => {
    const detected = detectIdentifierColumns(FEED, ATTRS);
    expect(suggestListColumn("ppid", detected, "product")).toBe("product.ppid");
    expect(suggestListColumn("isbn13", detected, "product")).toBe("product.isbn13");
    expect(suggestListColumn("ppid", detected.filter((d) => d.alias !== "product"), "product")).toBe("price.ppid");
    expect(suggestListColumn("other", detected, "product")).toBe("");
  });
  it("starts a new list clause on the detected identifier column", () => {
    const detected = detectIdentifierColumns(FEED, ATTRS);
    const c = emptyClause("list", { detected, primaryAlias: "product" });
    expect(c).toMatchObject({ kind: "list", idKind: "ppid", column: "product.ppid", values: [], invalid: [], suspicious: [] });
    expect(c.id).toMatch(/^c_/);
    expect(emptyClause("attribute")).toMatchObject({ kind: "attribute", column: "", category: "unknown", op: "eq", value: "" });
    expect(emptyClause("custom")).toMatchObject({ kind: "custom", sql: "" });
    expect(customClause("a = 1")).toMatchObject({ kind: "custom", sql: "a = 1" });
  });
});

describe("renameStaleReferences (UIFPE-568: pre-rename camelCase → live snake_case)", () => {
  const LIVE = {
    product: ["product_id", "item_type", "line_of_business", "isbn13", "description", "fulfillment_org_details"],
    price: ["product_id", "price_specifications"],
  };
  it("rewrites an attribute or list clause column that no longer resolves", () => {
    const clauses = [
      { id: "a", kind: "attribute", column: "product.itemType", category: "string", op: "eq", value: "BOOK" },
      { id: "l", kind: "list", column: "product.isbn13", idKind: "isbn13", values: ["9780000000001"] },
    ];
    const out = renameStaleReferences({ clauses }, LIVE);
    expect(out.clauses[0].column).toBe("product.item_type");
    expect(out.clauses[1]).toBe(clauses[1]); // already live — same object
    expect(out.renamed).toEqual([{ from: "product.itemType", to: "product.item_type" }]);
  });
  it("rewrites references inside custom SQL but never inside string literals", () => {
    const clauses = [{ id: "c", kind: "custom", sql: "product.lineOfBusiness = 'product.lineOfBusiness' AND price.priceSpecifications IS NOT NULL" }];
    const out = renameStaleReferences({ clauses }, LIVE);
    expect(out.clauses[0].sql).toBe(
      "product.line_of_business = 'product.lineOfBusiness' AND price.price_specifications IS NOT NULL"
    );
    expect(out.renamed.map((r) => r.to)).toEqual(["product.line_of_business", "price.price_specifications"]);
  });
  it("rewrites mapping sources and expressions the same way", () => {
    const mappings = [
      { type: "one_to_one", source: "product.itemType", target: "itemType" },
      { type: "many_to_one", sources: ["product.productId", "product.isbn13"], expression: "concat(product.productId, product.isbn13)", target: "key" },
      { type: "literal", target: "x", value: 1 },
    ];
    const out = renameStaleReferences({ mappings }, LIVE);
    expect(out.mappings[0].source).toBe("product.item_type");
    expect(out.mappings[0].target).toBe("itemType"); // targets are the partner's names — untouched
    expect(out.mappings[1].sources).toEqual(["product.product_id", "product.isbn13"]);
    expect(out.mappings[1].expression).toBe("concat(product.product_id, product.isbn13)");
    expect(out.mappings[2]).toBe(mappings[2]);
  });
  it("leaves a name that resolves in any case, an unknown alias and an ambiguous match alone", () => {
    const live = { product: ["item_type", "itemtype", "isbn13"] };
    const clauses = [
      { id: "1", kind: "attribute", column: "product.ISBN13", category: "string", op: "eq", value: "x" },
      { id: "2", kind: "attribute", column: "ghost.itemType", category: "string", op: "eq", value: "x" },
      { id: "3", kind: "attribute", column: "product.itemType", category: "string", op: "eq", value: "x" },
    ];
    const out = renameStaleReferences({ clauses }, live);
    expect(out.clauses).toBe(clauses); // nothing to do → the same array back
    expect(out.renamed).toEqual([]);
  });
  it("does nothing before live columns land", () => {
    const clauses = [{ id: "1", kind: "attribute", column: "product.itemType", category: "string", op: "eq", value: "x" }];
    expect(renameStaleReferences({ clauses }, { product: [] }).clauses).toBe(clauses);
    expect(renameStaleReferences({ clauses }, {}).clauses).toBe(clauses);
  });
});

describe("filterContext / columnGlueType", () => {
  it("derives aliases, columns, types and join types once", () => {
    expect(CTX.aliases).toEqual(["product", "price", "stock"]);
    expect(CTX.primaryAlias).toBe("product");
    expect(CTX.columnsByAlias.price).toEqual(["ppid", "vatCountry", "priceSpecifications"]);
    expect(CTX.typesByAlias.price).toBeNull();
    expect(CTX.joinTypeByAlias).toEqual({ product: null, price: "left", stock: "inner" });
  });
  it("carries the Gold master's attribute descriptions per alias, null when the API sent none (UIFPE-562)", () => {
    expect(CTX.descriptionsByAlias).toEqual({ product: null, price: null, stock: null });
    const text = "International Standard Book Number auto-assigned to items in PDH.";
    const ctx = filterContext([entry(PRODUCT, "product")], {
      [PRODUCT.id]: { ...live(["isbn13"], { isbn13: "string" }), descriptions: { isbn13: text } },
    });
    expect(ctx.descriptionsByAlias.product).toEqual({ isbn13: text });
  });
  it("resolves a column's Glue type case-insensitively, null when unknown", () => {
    expect(columnGlueType("product.price", CTX)).toBe("decimal(10,2)");
    expect(columnGlueType("product.PRICE", CTX)).toBe("decimal(10,2)");
    expect(columnGlueType("price.vatCountry", CTX)).toBeNull();
    expect(columnGlueType("ghost.x", CTX)).toBeNull();
    expect(columnGlueType("", CTX)).toBeNull();
  });
});

describe("unknownQualifiers / unqualifiedColumns", () => {
  it("reports qualifiers that are not feed aliases, first dotted segment only", () => {
    expect(unknownQualifiers("prise.vatCountry = 'GB' AND product.title = 'x'", ["product"])).toEqual(["prise"]);
    expect(unknownQualifiers("price.priceSpecifications.currency = 'GBP'", ["price"])).toEqual([]);
    expect(unknownQualifiers("product.title = 'a.b'", ["product"])).toEqual([]);
    expect(unknownQualifiers("product.title = 'it\\'s a.b'", ["product"])).toEqual([]);
  });
  it("finds bare column names the engine would not check", () => {
    expect(unqualifiedColumns("title = 'BOOK'", CTX.columnsByAlias)).toEqual([{ alias: "product", column: "title" }]);
    expect(unqualifiedColumns("TITLE = 'x'", CTX.columnsByAlias)).toEqual([{ alias: "product", column: "title" }]);
    expect(unqualifiedColumns("vatCountry = 'GB'", CTX.columnsByAlias)).toEqual([{ alias: "price", column: "vatCountry" }]);
    expect(unqualifiedColumns("lower(product.title) = 'x'", CTX.columnsByAlias)).toEqual([]);
    expect(unqualifiedColumns("size(product.programPpid) > 0", CTX.columnsByAlias)).toEqual([]);
    expect(unqualifiedColumns("title = 'BOOK'", {})).toEqual([]);
  });
});

describe("clauseIssues", () => {
  const big = (n) => Array.from({ length: n }, (_, i) => `A${i}`);
  it("blocks an over-cap list and passes one at the cap", () => {
    expect(blocks(clauseIssues(list(big(LIST_CAP + 1)), CTX))[0]).toMatch(/5,000 identifiers is the limit/);
    expect(blocks(clauseIssues(list(big(LIST_CAP)), CTX))).toEqual([]);
  });
  it("blocks an empty list, warns about ignored invalid and unusual identifiers", () => {
    expect(blocks(clauseIssues(list([]), CTX))).toEqual(["Paste or upload at least one identifier"]);
    const noisy = list(["A1", "B2"], {
      invalid: [{ value: "x", reason: "unexpected character" }],
      suspicious: [{ value: "B2", reason: "does not start with 'A'" }],
    });
    expect(blocks(clauseIssues(noisy, CTX))).toEqual([]);
    expect(warns(clauseIssues(noisy, CTX))).toEqual([
      "1 invalid PPID ignored — fix or remove it",
      "1 PPID looks unusual (does not start with 'A') — kept and matched exactly",
    ]);
  });
  it("blocks a clause whose source left the feed instead of dropping it", () => {
    expect(blocks(clauseIssues(list(["A1"], { column: "ghost.ppid" }), CTX))).toEqual([
      "Source «ghost» is no longer in the feed — remove this condition or re-add the source",
    ]);
  });
  it("warns about a column missing from the live list, unless the list is unknown", () => {
    expect(warns(clauseIssues(attr({ column: "product.nope" }), CTX))).toEqual([
      "«nope» is not in the live column list for product",
    ]);
    const blindCtx = filterContext([entry(PRICE, "price")], {});
    expect(clauseIssues(attr({ column: "price.anything" }), blindCtx)).toEqual([]);
  });
  it("blocks incomplete attribute clauses with the reason", () => {
    expect(blocks(clauseIssues(attr({ column: "" }), CTX))).toEqual(["Choose a column"]);
    expect(blocks(clauseIssues(attr({ op: "gt" }), CTX))).toEqual(["Choose how to compare"]);
    expect(blocks(clauseIssues(attr({ value: "" }), CTX))).toEqual(["Enter a value — to match blanks use “is null”"]);
    expect(blocks(clauseIssues(attr({ column: "product.price", category: "number", op: "gt", value: "abc" }), CTX))).toEqual([
      "The value must be a number",
    ]);
    expect(blocks(clauseIssues(attr({ column: "product.pubDate", category: "date", op: "on", value: "Jan" }), CTX))).toEqual([
      "The value must be a date (YYYY-MM-DD)",
    ]);
    expect(blocks(clauseIssues(attr({ column: "product.price", category: "number", op: "between", value: "1", value2: "" }), CTX))).toEqual([
      "Enter both values",
    ]);
    expect(clauseIssues(attr({ op: "is_null", value: "" }), CTX)).toEqual([]);
  });
  it("checks custom SQL for blanks, foreign qualifiers and bare columns", () => {
    expect(blocks(clauseIssues(custom("  "), CTX))).toEqual(["Write a Spark SQL condition, or remove this row"]);
    expect(warns(clauseIssues(custom("prise.vatCountry = 'GB'"), CTX))).toEqual([
      "Mentions «prise», which is not a source alias (aliases: product, price, stock)",
    ]);
    expect(warns(clauseIssues(custom("title = 'BOOK'"), CTX))).toEqual([
      "«title» is not alias-qualified — write product.title; the engine does not check bare names",
    ]);
    expect(clauseIssues(custom("lower(product.title) = 'x' AND size(price.priceSpecifications) > 0"), CTX)).toEqual([]);
  });
});

describe("filterSetIssues / filterIssues / filtersComplete", () => {
  it("blocks a model too large for the store", () => {
    const huge = list(Array.from({ length: 20000 }, (_, i) => `A${String(i).padStart(12, "0")}`));
    expect(JSON.stringify({ version: 1, clauses: [huge] }).length).toBeGreaterThan(FILTER_BYTES_CAP);
    expect(blocks(filterSetIssues([huge]))[0]).toMatch(/too large to store/);
    expect(filterSetIssues([list(["A1"])])).toEqual([]);
  });
  it("aggregates per clause and tells the gate whether to block", () => {
    const r = filterIssues([list(["A1"]), attr({ value: "" })], CTX);
    expect(r.byId.l1).toEqual([]);
    expect(blocks(r.byId.a1)).toHaveLength(1);
    expect(r.blocking).toBe(true);
    expect(filtersComplete([], CTX)).toBe(true);
    expect(filtersComplete([list(["A1"], { invalid: [{ value: "x", reason: "r" }] })], CTX)).toBe(true);
    expect(filtersComplete([attr({ value: "" })], CTX)).toBe(false);
  });
});

describe("leftJoinHint", () => {
  it("flags a condition on a left-joined, non-driving source", () => {
    expect(leftJoinHint(list(["A1"], { column: "price.ppid" }), CTX)).toBe(
      "price is left-joined: product rows with no price match are excluded by this condition"
    );
    expect(leftJoinHint(attr({ column: "stock.qtyOnHand" }), CTX)).toBeNull();
    expect(leftJoinHint(attr(), CTX)).toBeNull();
    expect(leftJoinHint(custom("price.ppid = 'A1'"), CTX)).toBeNull();
  });
});

describe("upgradeClauseCategories", () => {
  it("moves an unknown-typed clause to its real category once types land", () => {
    const before = [attr({ column: "product.price", category: "unknown", op: "eq", value: "1" })];
    const after = upgradeClauseCategories(before, CTX);
    expect(after[0].category).toBe("number");
    expect(after).not.toBe(before);
  });
  it("leaves the author's operator alone when it does not exist in the new category", () => {
    const before = [attr({ column: "product.price", category: "unknown", op: "contains", value: "1" })];
    expect(upgradeClauseCategories(before, CTX)).toBe(before);
  });
  it("returns the same reference when nothing changes", () => {
    const before = [attr(), attr({ column: "price.vatCountry", category: "unknown" }), custom("a = 1")];
    expect(upgradeClauseCategories(before, CTX)).toBe(before);
  });
  it("upgrades a date or timestamp clause to 'is between' when its operator is not a date operator (owner decision 2026-09-22)", () => {
    const eq = [attr({ column: "product.publication_date", category: "unknown", op: "eq", value: "2024-01-01" })];
    expect(upgradeClauseCategories(eq, GOLD_CTX)[0]).toMatchObject({ category: "date", op: "between", value: "2024-01-01", value2: "" });
    const ts = [attr({ column: "product.ts", category: "unknown", op: "contains", value: "2024" })];
    expect(upgradeClauseCategories(ts, CTX)[0]).toMatchObject({ category: "timestamp", op: "between" });
    // a date operator the author already chose survives the upgrade
    const on = [attr({ column: "product.pubDate", category: "unknown", op: "on", value: "2024-01-01" })];
    expect(upgradeClauseCategories(on, CTX)[0]).toMatchObject({ category: "date", op: "on" });
    // is_null exists in both families: category moves, operator stays
    const isNull = [attr({ column: "product.pubDate", category: "unknown", op: "is_null", value: "" })];
    expect(upgradeClauseCategories(isNull, CTX)[0]).toMatchObject({ category: "date", op: "is_null" });
  });
});

describe("attributeClauseForColumn (the column change)", () => {
  const fresh = () => ({ ...emptyClause("attribute"), id: "a1" });
  it("defaults a date or timestamp column to 'is between' — a from/to range, not a value list", () => {
    expect(attributeClauseForColumn(fresh(), "product.publication_date", GOLD_CTX)).toEqual({
      id: "a1",
      kind: "attribute",
      column: "product.publication_date",
      category: "date",
      op: "between",
      value: "",
      value2: "",
    });
    expect(attributeClauseForColumn(fresh(), "product.ts", CTX)).toMatchObject({ category: "timestamp", op: "between" });
    expect(operatorsFor("date").find((o) => o.op === "between")).toMatchObject({ arity: 2, input: "date" });
  });
  it("keeps a date operator the author had when moving between date columns", () => {
    const onDate = attr({ column: "product.pubDate", category: "date", op: "on", value: "2024-01-01" });
    expect(attributeClauseForColumn(onDate, "product.publication_date", GOLD_CTX)).toMatchObject({
      category: "date",
      op: "on",
      value: "",
      value2: "",
    });
  });
  it("keeps the operator when the new category has it, else takes that category's first", () => {
    expect(attributeClauseForColumn(fresh(), "product.list_price", GOLD_CTX)).toMatchObject({ category: "number", op: "eq" });
    const between = attr({ column: "product.publication_date", category: "date", op: "between", value: "a", value2: "b" });
    expect(attributeClauseForColumn(between, "product.list_price", GOLD_CTX)).toMatchObject({
      category: "number",
      op: "between",
      value: "",
      value2: "",
    });
    const onDate = attr({ column: "product.publication_date", category: "date", op: "on" });
    expect(attributeClauseForColumn(onDate, "product.list_price", GOLD_CTX)).toMatchObject({ category: "number", op: "eq" });
    expect(attributeClauseForColumn(onDate, "product.is_digital", GOLD_CTX)).toMatchObject({ category: "boolean", op: "is_true" });
    expect(attributeClauseForColumn(fresh(), "product.item_type", GOLD_CTX)).toMatchObject({ category: "string", op: "eq" });
    expect(attributeClauseForColumn(fresh(), "product.associated_programs", GOLD_CTX)).toMatchObject({
      category: "array",
      op: "has_value",
    });
  });
  it("falls back to unknown (string operators) when the type is not known", () => {
    expect(attributeClauseForColumn(fresh(), "price.vatCountry", CTX)).toMatchObject({ category: "unknown", op: "eq" });
    expect(attributeClauseForColumn(fresh(), "ghost.x", CTX)).toMatchObject({ column: "ghost.x", category: "unknown", op: "eq" });
  });
  it("makes a clause the gate accepts only once both dates are in", () => {
    const c = attributeClauseForColumn(fresh(), "product.publication_date", GOLD_CTX);
    expect(isComplete(c)).toBe(false);
    expect(blocks(clauseIssues(c, GOLD_CTX))).toEqual(["Enter both values"]);
    expect(isComplete({ ...c, value: "2024-01-01" })).toBe(false);
    const done = { ...c, value: "2024-01-01", value2: "2024-12-31" };
    expect(isComplete(done)).toBe(true);
    expect(compileClause(done)).toBe("product.publication_date BETWEEN to_date('2024-01-01') AND to_date('2024-12-31')");
    expect(describeClause(done)).toBe("product.publication_date is between 2024-01-01 and 2024-12-31");
  });
});

describe("filterRuleLabel / filterRuleDefinition (UIFPE-717)", () => {
  it("labels a filter by its position and what it keeps", () => {
    expect(filterRuleLabel(attr(), 0)).toBe("Filter 1 · product.title equals 'BOOK'");
    expect(filterRuleLabel(list(["A1", "A2"]), 1)).toBe("Filter 2 · product.ppid is one of A1, A2");
    expect(filterRuleLabel(custom("price.amount > 0"), 2)).toBe("Filter 3 · custom Spark SQL");
  });
  it("shows the Spark SQL a finished filter compiles to", () => {
    expect(filterRuleDefinition(attr())).toBe("product.title = 'BOOK'");
    expect(filterRuleDefinition(custom("price.amount > 0"))).toBe("price.amount > 0");
    const many = filterRuleDefinition(list(Array.from({ length: 8 }, (_, i) => `A${i}`)));
    expect(many).toContain("/* +3 more */");
  });
  it("says so when a filter is not finished yet", () => {
    expect(filterRuleDefinition(attr({ value: "" }))).toBe("incomplete — not applied until it is finished");
    expect(filterRuleDefinition(custom("   "))).toBe("incomplete — not applied until it is finished");
  });
});

describe("describeClause / describeFilters", () => {
  it("reads each kind back in plain English", () => {
    expect(describeClause(list(["A1", "A2"]))).toBe("product.ppid is one of A1, A2");
    expect(describeClause(list(Array.from({ length: 4812 }, (_, i) => `A${i}`)))).toBe("product.ppid is one of 4,812 PPIDs");
    expect(describeClause(list(["9780134685991"], { column: "product.isbn13", idKind: "isbn13" }))).toBe(
      "product.isbn13 is one of 9780134685991"
    );
    expect(describeClause(list([]))).toBe("product.ppid is one of … (no identifiers yet)");
    expect(describeClause(attr())).toBe("product.title equals 'BOOK'");
    expect(describeClause(attr({ column: "stock.qtyOnHand", category: "number", op: "gt", value: "0" }))).toBe(
      "stock.qtyOnHand is greater than 0"
    );
    expect(describeClause(attr({ column: "product.pubDate", category: "date", op: "on_or_after", value: "2024-01-01" }))).toBe(
      "product.pubDate is on or after 2024-01-01"
    );
    expect(describeClause(attr({ column: "product.price", category: "number", op: "between", value: "1", value2: "2" }))).toBe(
      "product.price is between 1 and 2"
    );
    expect(describeClause(attr({ column: "product.programPpid", category: "array", op: "is_empty", value: "" }))).toBe(
      "product.programPpid is empty"
    );
    expect(describeClause(attr({ value: "" }))).toBe("product.title equals … (incomplete)");
    expect(describeClause(custom("size(price.priceSpecifications) > 0"))).toBe("Spark SQL: size(price.priceSpecifications) > 0");
  });
  it("summarises only the complete clauses", () => {
    expect(describeFilters([list(["A1"]), attr({ value: "" }), custom("a = 1")])).toEqual([
      "product.ppid is one of A1",
      "Spark SQL: a = 1",
    ]);
  });
});

describe("filtersFromConfig", () => {
  const model = (clauses) => ({ version: 1, clauses });
  it("uses the saved model when it recompiles to exactly the published filters", () => {
    const clauses = [list(["A1", "A2"]), attr()];
    const cfg = { filters: compileFilters(clauses) };
    const r = filtersFromConfig(cfg, { filter_model: model(clauses) });
    expect(r.provenance).toBe("model");
    expect(r.clauses).toEqual(clauses);
  });
  it("falls back to custom clauses when the model does not match what was published", () => {
    const r = filtersFromConfig({ filters: ["product.ppid IN ('A9')"] }, { filter_model: model([list(["A1"])]) });
    expect(r.provenance).toBe("filters");
    expect(r.clauses.map((c) => c.kind)).toEqual(["custom"]);
    expect(r.clauses[0].sql).toBe("product.ppid IN ('A9')");
  });
  it("keeps EVERY published filter entry (regression: the old prefill kept only filters[0])", () => {
    const r = filtersFromConfig({ filters: ["a = 1", "b = 2"] }, {});
    expect(r.provenance).toBe("filters");
    expect(r.clauses.map((c) => c.sql)).toEqual(["a = 1", "b = 2"]);
    expect(r.clauses.every((c) => c.kind === "custom" && c.id)).toBe(true);
  });
  it("reads the pre-builder settings.row_filter, then gives up gracefully", () => {
    expect(filtersFromConfig({ filters: [] }, { row_filter: " x = 1 " })).toEqual({
      clauses: [expect.objectContaining({ kind: "custom", sql: "x = 1" })],
      provenance: "legacy",
    });
    expect(filtersFromConfig({}, {})).toEqual({ clauses: [], provenance: "none" });
    expect(filtersFromConfig(undefined, undefined)).toEqual({ clauses: [], provenance: "none" });
  });
  it("drops a malformed model rather than showing structure the engine never ran", () => {
    const bad = model([{ kind: "regex", pattern: "x" }, list(["A1"])]);
    const r = filtersFromConfig({ filters: ["product.ppid IN ('A1')"] }, { filter_model: bad });
    expect(r.provenance).toBe("filters");
  });
  it("gives a saved clause an id when it has none", () => {
    const saved = { kind: "custom", sql: "a = 1" };
    const r = filtersFromConfig({ filters: ["a = 1"] }, { filter_model: model([saved]) });
    expect(r.provenance).toBe("model");
    expect(r.clauses[0].id).toMatch(/^c_/);
  });
});

describe("filterCheckConfig / filterCheckIssues", () => {
  it("sends the real sources and joins with a stub mapping so the filter alone is checked", () => {
    const cfg = filterCheckConfig(FEED, ["product.ppid IN ('A1')"]);
    expect(cfg.sources.map((s) => s.name)).toEqual(["product", "price", "stock"]);
    expect(cfg.joins).toHaveLength(2);
    expect(cfg.mappings).toEqual([FILTER_CHECK_STUB_MAPPING]);
    expect(cfg.filters).toEqual(["product.ppid IN ('A1')"]);
  });
  it("routes the validator's issues to clauses, to the general area, or counts them", () => {
    const r = filterCheckIssues([
      { where: "filters[1]", message: "function 'foo' is not on the allowlist" },
      { where: "filters[1]", message: "column 'x' not found in source 'product' (has: ppid)" },
      { where: "engine", message: "dynamic_mapping is not installed" },
      { where: "api", message: "Failed to fetch" },
      { where: "joins[0].condition", message: "unknown column" },
    ]);
    expect(r.byIndex).toEqual({ 1: ["function 'foo' is not on the allowlist", "column 'x' not found in source 'product' (has: ppid)"] });
    expect(r.general).toEqual(["dynamic_mapping is not installed", "Failed to fetch"]);
    expect(r.otherCount).toBe(1);
    expect(filterCheckIssues([])).toEqual({ byIndex: {}, general: [], otherCount: 0 });
  });
});

describe("suggestionsEligible", () => {
  it("offers suggestions for text and number conditions with a value box", () => {
    expect(suggestionsEligible(attr(), CTX)).toEqual({ eligible: true, reason: null });
    expect(suggestionsEligible(attr({ column: "product.price", category: "number", op: "gt", value: "" }), CTX)).toEqual({
      eligible: true,
      reason: null,
    });
    expect(suggestionsEligible(attr({ column: "price.vatCountry", category: "unknown" }), CTX).eligible).toBe(true);
  });
  it("says why identifier and structured columns get none", () => {
    expect(suggestionsEligible(attr({ column: "product.ppid" }), CTX).reason).toMatch(/identifier list/);
    expect(suggestionsEligible(attr({ column: "product.isbn13" }), CTX).reason).toMatch(/identifier list/);
    expect(suggestionsEligible(attr({ column: "product.programPpid", category: "array", op: "has_value" }), CTX).reason).toMatch(
      /structured/
    );
  });
  it("refuses the Gold identifiers by name and text over the cap per the profile, allows the rest (UIFPE-514)", () => {
    const profiles = { product: PRODUCT_PROFILE };
    expect(suggestionsEligible(attr({ column: "product.product_id" }), GOLD_CTX, profiles).reason).toMatch(/identifier list/);
    expect(suggestionsEligible(attr({ column: "product.inventory_item_id", category: "number" }), GOLD_CTX, profiles).reason).toMatch(
      /identifier list/
    );
    expect(suggestionsEligible(attr({ column: "product.source_item_number" }), GOLD_CTX).reason).toMatch(/identifier list/);
    expect(suggestionsEligible(attr({ column: "program.program_id" }), GOLD_CTX).reason).toMatch(/identifier list/);
    const overCap = suggestionsEligible(attr({ column: "product.description" }), GOLD_CTX, profiles);
    expect(overCap.eligible).toBe(false);
    expect(overCap.reason).toMatch(/more than 1,000 distinct values/);
    expect(overCap.reason).toMatch(/Custom Spark SQL/);
    expect(suggestionsEligible(attr({ column: "product.DESCRIPTION" }), GOLD_CTX, profiles).eligible).toBe(false);
    expect(suggestionsEligible(attr({ column: "product.item_type" }), GOLD_CTX, profiles)).toEqual({ eligible: true, reason: null });
    expect(suggestionsEligible(attr({ column: "product.brand" }), GOLD_CTX, profiles).eligible).toBe(true);
    // a number over the cap is refused too: the API applies the same cap to every column
    expect(suggestionsEligible(attr({ column: "product.list_price", category: "number", op: "gt" }), GOLD_CTX, profiles).eligible).toBe(false);
    // no profile yet, or a pending/failed one: the request proceeds as before
    expect(suggestionsEligible(attr({ column: "product.description" }), GOLD_CTX).eligible).toBe(true);
    expect(suggestionsEligible(attr({ column: "product.description" }), GOLD_CTX, { product: { status: "pending" } }).eligible).toBe(true);
    expect(
      suggestionsEligible(attr({ column: "product.description" }), GOLD_CTX, { product: { status: "failed", reason: "x" } }).eligible
    ).toBe(true);
  });
  it("is silent where there is no value box or no known source", () => {
    expect(suggestionsEligible(attr({ op: "is_null" }), CTX)).toEqual({ eligible: false, reason: null });
    expect(suggestionsEligible(attr({ column: "product.flag", category: "boolean", op: "is_true" }), CTX).eligible).toBe(false);
    expect(suggestionsEligible(attr({ column: "product.pubDate", category: "date", op: "on" }), CTX).eligible).toBe(false);
    expect(suggestionsEligible(attr({ column: "ghost.x" }), CTX).eligible).toBe(false);
    expect(suggestionsEligible(list(["A1"]), CTX).eligible).toBe(false);
    expect(suggestionsEligible(custom("a = 1"), CTX).eligible).toBe(false);
  });
});

describe("previewRequest / previewSignature / listIndexOf", () => {
  const clauses = [list(["A1", "A2"]), attr({ value: "" }), attr({ id: "a2" }), list([], { id: "l2" }), list(["9780134685991"], { id: "l3", column: "product.isbn13", idKind: "isbn13" })];
  it("sends the Check-filter config plus the complete identifier lists, in order", () => {
    const request = previewRequest(FEED, clauses);
    expect(request.mapping_config.mappings).toEqual([FILTER_CHECK_STUB_MAPPING]);
    expect(request.mapping_config.filters).toEqual([
      "product.ppid IN ('A1', 'A2')",
      "product.title = 'BOOK'",
      "product.isbn13 IN ('9780134685991')",
    ]);
    expect(request.lists).toEqual([
      { column: "product.ppid", values: ["A1", "A2"] },
      { column: "product.isbn13", values: ["9780134685991"] },
    ]);
  });
  // UIFPE-603: the sample is the export — the real mappings ride along, and
  // a mapping edit changes the signature (the last result goes stale)
  it("sends the real mappings when there are any, so the sample is the export's columns", () => {
    const mappings = [{ type: "one_to_one", source: "product.title", target: "Title" }];
    const request = previewRequest(FEED, clauses, mappings);
    expect(request.mapping_config.mappings).toEqual(mappings);
    expect(previewSignature(request)).not.toBe(previewSignature(previewRequest(FEED, clauses)));
    expect(previewRequest(FEED, clauses, []).mapping_config.mappings).toEqual([FILTER_CHECK_STUB_MAPPING]);
  });
  it("positions a list clause among the complete lists only", () => {
    expect(listIndexOf(clauses, clauses[0])).toBe(0);
    expect(listIndexOf(clauses, clauses[4])).toBe(1);
    expect(listIndexOf(clauses, clauses[3])).toBe(-1); // incomplete: not sent
    expect(listIndexOf(clauses, clauses[2])).toBe(-1); // not a list
  });
  it("signs the request so an edit marks a result stale", () => {
    const a = previewSignature(previewRequest(FEED, clauses));
    const b = previewSignature(previewRequest(FEED, [...clauses, custom("a = 1")]));
    expect(a).toBe(previewSignature(previewRequest(FEED, clauses)));
    expect(a).not.toBe(b);
  });
});

describe("attributeColumnsFor / cardinalityLabel / describeHidden", () => {
  // the pre-514 profile shape: no status, `columns` only — still read as done
  const PROFILE = {
    rows: 1000,
    columns: {
      ppid: { distinct: 1000, unique_like: true, identifier: true },
      title: { distinct: 990, unique_like: true, identifier: false },
      price: { distinct: 120, unique_like: false, identifier: false },
      flag: { distinct: 2, unique_like: false, identifier: false },
    },
  };
  it("hides identifiers by name; a text column under the cap is offered whatever unique_like says", () => {
    const { offered, hidden, pending, unmeasured } = attributeColumnsFor(CTX, { product: PROFILE });
    // UIFPE-514: title with distinct 990 of 1,000 rows was "unique_like → hidden";
    // the ratio measured the wrong grain, so under the cap it is OFFERED
    expect(offered.product).toEqual(["title", "price", "pubDate", "ts", "flag", "programPpid", "spec"]);
    expect(hidden.product).toEqual([
      { column: "ppid", reason: "identifier" },
      { column: "isbn13", reason: "identifier" },
    ]);
    expect(pending.product).toEqual([]);
    // no profile for price yet, and no type map either: every non-identifier
    // column is unknown-typed, so text, so held back until the profile lands
    expect(offered.price).toEqual([]);
    expect(pending.price).toEqual(["vatCountry", "priceSpecifications"]);
    expect(hidden.price).toEqual([{ column: "ppid", reason: "identifier" }]);
    // stock has types but no profile: the number appears at once, the text waits
    expect(offered.stock).toEqual(["qtyOnHand"]);
    expect(pending.stock).toEqual(["organizationCode"]);
    expect(unmeasured).toEqual({});
  });
  it("labels measured columns", () => {
    expect(cardinalityLabel(PROFILE, "price")).toBe("~120 values");
    expect(cardinalityLabel(PROFILE, "flag")).toBe("~2 values");
    expect(cardinalityLabel(PROFILE, "title")).toBe("~990 values"); // was "unique per product" before UIFPE-514
    expect(cardinalityLabel(PROFILE, "nope")).toBe("");
    expect(cardinalityLabel(null, "price")).toBe("");
    expect(cardinalityLabel({ status: "pending" }, "price")).toBe("");
    expect(cardinalityLabel({ status: "failed", reason: "x" }, "price")).toBe("");
  });
  it("writes one footnote for what was hidden", () => {
    const { hidden } = attributeColumnsFor(CTX, { product: PROFILE });
    expect(describeHidden(hidden)).toBe(
      "Not offered here: product.ppid, product.isbn13, price.ppid, stock.ppid — identifiers: use an identifier list."
    );
    expect(describeHidden({ product: [] })).toBe("");
    expect(describeHidden(undefined)).toBe("");
  });
});

// UIFPE-514: the picker offers only attributes a person could pick a value
// for, on the Gold tables as they are actually spelled.
describe("attributeColumnsFor on Gold (UIFPE-514)", () => {
  const GOLD_IDENTIFIERS = {
    product: ["inventory_item_id", "product_id", "isbn13", "isbn10", "source_item_number"],
    price: ["product_id", "isbn13"],
    stock: ["product_id"],
    program: ["program_id"],
  };
  const identifiers = (hidden) =>
    Object.fromEntries(Object.entries(hidden).map(([a, l]) => [a, l.filter((h) => h.reason === "identifier").map((h) => h.column)]));

  it("never offers the identifier columns by name — on every table, whatever their type or profile", () => {
    for (const profiles of [{}, { product: PRODUCT_PROFILE }, { product: { status: "failed", reason: "timeout" } }]) {
      const { offered, hidden } = attributeColumnsFor(GOLD_CTX, profiles);
      expect(identifiers(hidden)).toEqual(GOLD_IDENTIFIERS);
      for (const [alias, cols] of Object.entries(GOLD_IDENTIFIERS)) {
        for (const col of cols) expect(offered[alias]).not.toContain(col);
      }
    }
    // inventory_item_id is a bigint: hidden by name BEFORE the type rule
    expect(attributeColumnsFor(GOLD_CTX, {}).offered.product).not.toContain("inventory_item_id");
  });
  it("pins the widened pattern: whole name, case-insensitive, history spellings kept", () => {
    for (const name of [
      "product_id", "parent_product_id", "inventory_item_id", "source_item_number", "program_id", "content_id",
      "isbn13", "isbn10", "ppid", "isbn", "isbn_13", "isbn_10", "PRODUCT_ID", "Isbn13",
    ]) {
      expect(IDENTIFIER_COLUMN_RE.test(name)).toBe(true);
    }
    for (const name of ["product_ids", "my_product_id", "organization_id", "program_family_catalog_code", "description", "id"]) {
      expect(IDENTIFIER_COLUMN_RE.test(name)).toBe(false);
    }
    expect(TEXT_CARDINALITY_CAP).toBe(1000);
  });
  it("with a done profile: hides text over the cap, offers text at or under it with a label, and everything typed", () => {
    const { offered, hidden, pending, unmeasured } = attributeColumnsFor(GOLD_CTX, { product: PRODUCT_PROFILE });
    expect(offered.product).toEqual([
      "organization_id",
      "item_type",
      "brand",
      "list_price",
      "publication_date",
      "is_digital",
      "associated_programs",
    ]);
    expect(hidden.product.filter((h) => h.reason === "text")).toEqual([
      { column: "description", reason: "text" },
      { column: "long_description", reason: "text" },
      { column: "cover_image_large_url", reason: "text" },
    ]);
    expect(pending.product).toEqual([]);
    expect(unmeasured.product).toBeUndefined();
    // labels: text under the cap says how many; over the cap says so; unmeasured says nothing
    expect(cardinalityLabel(PRODUCT_PROFILE, "item_type")).toBe("~5 values");
    expect(cardinalityLabel(PRODUCT_PROFILE, "brand")).toBe("~300 values");
    expect(cardinalityLabel(PRODUCT_PROFILE, "description")).toBe("more than 1,000 values");
    expect(cardinalityLabel(PRODUCT_PROFILE, "organization_id")).toBe("~12 values");
    expect(cardinalityLabel(PRODUCT_PROFILE, "associated_programs")).toBe("");
  });
  it("offers numbers, dates and booleans regardless of how many distinct values they hold", () => {
    // list_price 48,000 and publication_date 9,000 are far over the cap; a range needs no value list
    const { offered } = attributeColumnsFor(GOLD_CTX, { product: PRODUCT_PROFILE });
    expect(offered.product).toEqual(expect.arrayContaining(["organization_id", "list_price", "publication_date", "is_digital"]));
    const exactlyCap = { status: "done", rows: 5000, columns: { brand: { distinct: 1000 }, item_type: { distinct: 1001 } } };
    const r = attributeColumnsFor(GOLD_CTX, { product: exactlyCap });
    expect(r.offered.product).toContain("brand"); // at the cap is offered
    expect(r.hidden.product).toContainEqual({ column: "item_type", reason: "text" }); // one over is not
    // a text column the done profile has no entry for is offered
    expect(r.offered.product).toContain("description");
  });
  it("with no profile: text columns are pending, everything else appears at once", () => {
    const { offered, hidden, pending, unmeasured } = attributeColumnsFor(GOLD_CTX, {});
    expect(offered.product).toEqual(["organization_id", "list_price", "publication_date", "is_digital", "associated_programs"]);
    expect(pending.product).toEqual(["description", "long_description", "item_type", "brand", "cover_image_large_url"]);
    expect(hidden.product.filter((h) => h.reason === "text")).toEqual([]);
    expect(offered.price).toEqual(["price_specifications"]);
    expect(pending.price).toEqual(["vat_country"]);
    expect(offered.stock).toEqual(["on_hand_quantity"]);
    expect(pending.stock).toEqual(["organization_code"]);
    expect(offered.program).toEqual([]);
    expect(pending.program).toEqual(["description", "program_family_catalog_code", "inventory_item_status"]);
    expect(unmeasured).toEqual({});
    expect(describeMeasuring(pending)).toBe("measuring 10 more attributes…");
    // a pending profile (POST accepted) holds back the same way as none
    expect(attributeColumnsFor(GOLD_CTX, { product: { status: "pending" } }).pending.product).toEqual(pending.product);
  });
  it("describeMeasuring counts across sources and goes quiet when nothing is held back", () => {
    expect(describeMeasuring({ product: ["a"], price: [] })).toBe("measuring 1 more attribute…");
    expect(describeMeasuring({ product: ["a", "b"], price: ["c"] })).toBe("measuring 3 more attributes…");
    expect(describeMeasuring({ product: [] })).toBe("");
    expect(describeMeasuring({})).toBe("");
    expect(describeMeasuring(undefined)).toBe("");
    const done = attributeColumnsFor(GOLD_CTX, {
      product: PRODUCT_PROFILE,
      price: { status: "done", columns: {} },
      stock: { status: "done", columns: {} },
      program: { status: "failed", reason: "Athena timed out" },
    });
    expect(describeMeasuring(done.pending)).toBe("");
  });
  it("with a failed or unavailable profile: text columns fall back to offered and the reason is carried, never silent", () => {
    const failed = { product: { status: "failed", reason: "Athena timed out after 20 s" } };
    const { offered, hidden, pending, unmeasured } = attributeColumnsFor(GOLD_CTX, failed);
    expect(offered.product).toEqual([
      "organization_id",
      "description",
      "long_description",
      "item_type",
      "brand",
      "list_price",
      "publication_date",
      "is_digital",
      "cover_image_large_url",
      "associated_programs",
    ]);
    expect(pending.product).toEqual([]);
    expect(hidden.product.map((h) => h.column)).toEqual(GOLD_IDENTIFIERS.product);
    expect(unmeasured).toEqual({ product: { reason: "Athena timed out after 20 s" } });
    // "unavailable" is a failure with a reason too; a reasonless failure still says something
    const unavailable = attributeColumnsFor(GOLD_CTX, { product: { status: "unavailable", reason: "preview is not configured" } });
    expect(unavailable.unmeasured.product).toEqual({ reason: "preview is not configured" });
    expect(unavailable.offered.product).toContain("description");
    expect(attributeColumnsFor(GOLD_CTX, { product: { status: "failed" } }).unmeasured.product.reason).toBeTruthy();
    // suggestions are not refused by a failed profile (no measurement to refuse on)
    expect(cardinalityLabel(failed.product, "item_type")).toBe("");
  });
  it("reads the legacy shape (columns, no status) as done", () => {
    const legacy = { rows: PRODUCT_PROFILE.rows, columns: PRODUCT_PROFILE.columns };
    const r = attributeColumnsFor(GOLD_CTX, { product: legacy });
    expect(r.pending.product).toEqual([]);
    expect(r.hidden.product).toContainEqual({ column: "description", reason: "text" });
    expect(r.offered.product).toContain("brand");
  });
  it("writes the footnote's text sentence verbatim", () => {
    const { hidden } = attributeColumnsFor(GOLD_CTX, { product: PRODUCT_PROFILE });
    expect(describeHidden(hidden)).toBe(
      "Not offered here: product.inventory_item_id, product.product_id, product.isbn13, product.isbn10, product.source_item_number, price.product_id, price.isbn13, stock.product_id, program.program_id — identifiers: use an identifier list. " +
        "product.description, product.long_description, product.cover_image_large_url — more than 1,000 values: use an identifier list to pick products, or Custom Spark SQL for a text search."
    );
    expect(describeHidden({ product: [{ column: "description", reason: "text" }] })).toBe(
      "Not offered here: product.description — more than 1,000 values: use an identifier list to pick products, or Custom Spark SQL for a text search."
    );
  });
});

describe("describePreviewCounts / missingFor / fmtBytes", () => {
  it("headlines the matched count only — no total, no percentage (owner decision 2026-09-21)", () => {
    expect(describePreviewCounts(6830112, 12480)).toBe("12,480 rows match");
    expect(describePreviewCounts(2173233, 9)).toBe("9 rows match");
    expect(describePreviewCounts(1000000, 1)).toBe("1 row matches");
    expect(describePreviewCounts(1000, 0)).toBe("No rows match");
    // an empty join is a different problem from a filter nothing matches
    expect(describePreviewCounts(0, 0)).toBe("The joined sources hold no rows");
    expect(describePreviewCounts(undefined, undefined)).toBe("The joined sources hold no rows");
  });
  it("normalises the not-found entry", () => {
    const result = { missing: [{ column: "product.isbn13", missing: ["a", "b"], truncated: false }] };
    expect(missingFor(result, 0)).toEqual({ column: "product.isbn13", missing: ["a", "b"], truncated: false });
    expect(missingFor(result, 1)).toBeNull();
    expect(missingFor(null, 0)).toBeNull();
    expect(missingFor({ missing: [{}] }, 0)).toEqual({ column: null, missing: [], truncated: false });
  });
  it("formats scanned bytes", () => {
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(20 * 1024)).toBe("20 KB");
    expect(fmtBytes(1.26 * 1024 * 1024 * 1024)).toBe("1.26 GB");
    expect(fmtBytes(3.5 * 1024 * 1024)).toBe("3.5 MB");
  });
});

// UIFPE-490: the matched rows as one CSV beside the preview
describe("canDownload / downloadFor / describeDownload", () => {
  const done = { status: "done", signature: "sig-a", matched: 12480, total: 6830112 };

  it("offers the button only for a finished, current preview with matches", () => {
    expect(canDownload(done, false)).toEqual({ ok: true, reason: null });
    expect(canDownload(null, false)).toEqual({ ok: false, reason: null });
    expect(canDownload({ status: "running" }, false)).toEqual({ ok: false, reason: null });
    expect(canDownload({ status: "failed" }, false)).toEqual({ ok: false, reason: null });
    expect(canDownload(done, true).ok).toBe(false);
    expect(canDownload(done, true).reason).toMatch(/run Preview matches again/);
    expect(canDownload({ ...done, matched: 0 }, false).reason).toMatch(/nothing to download/);
    expect(canDownload({ ...done, matched: undefined }, false).ok).toBe(false);
  });

  it("shows a download only for the preview result it was asked for", () => {
    const mine = { status: "done", signature: "sig-a", url: "https://signed" };
    expect(downloadFor(mine, done, false)).toBe(mine);
    // an edit changed the request: the preview is stale, the link is hidden
    expect(downloadFor(mine, done, true)).toBeNull();
    // a preview rerun with a different filter: signatures differ
    expect(downloadFor({ ...mine, signature: "sig-b" }, done, false)).toBeNull();
    // no finished preview to attach to
    expect(downloadFor(mine, { ...done, status: "running" }, false)).toBeNull();
    expect(downloadFor(null, done, false)).toBeNull();
    expect(downloadFor(mine, null, false)).toBeNull();
    // a running download for the current preview still shows (its progress)
    expect(downloadFor({ status: "running", signature: "sig-a", id: "q" }, done, false)).not.toBeNull();
  });

  it("labels the link with the rows it carries and says when the cap cut it", () => {
    expect(describeDownload(12480, 100000, 3.5 * 1024 * 1024)).toEqual({
      rows: 12480,
      truncated: false,
      label: "Download CSV — 12,480 rows · 3.5 MB",
    });
    expect(describeDownload(1, 100000)).toEqual({ rows: 1, truncated: false, label: "Download CSV — 1 row" });
    expect(describeDownload(2345678, 100000, null)).toEqual({
      rows: 100000,
      truncated: true,
      label: "Download CSV — 100,000 of 2,345,678 matched rows (capped)",
    });
    // the API's cap wins over the mirrored constant; a missing cap falls back to it
    expect(describeDownload(250, 100).truncated).toBe(true);
    expect(describeDownload(250, undefined).truncated).toBe(false);
    expect(describeDownload(250, 0).rows).toBe(250);
  });
});
