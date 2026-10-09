// UIFPE-639: "What this export sends" — read from a published version, and
// never showing a value the version or run does not hold.
import { describe, expect, it } from "vitest";
import {
  fieldKindsText,
  lastRunFigures,
  recipeFields,
  recipeFilters,
  recipeGrain,
  recipeSentence,
  recipeSources,
  recipeSpreads,
  searchFields,
} from "../exportRecipe.js";

const MC = {
  sources: [
    { name: "product", path: "glue_catalog.uif_echo_dev_gold_product.product", format: "iceberg" },
    { name: "price", path: "glue_catalog.uif_echo_dev_gold_price.price", format: "iceberg" },
  ],
  joins: [{ left: "product", right: "price", type: "left", condition: "product.product_id = price.product_id" }],
  unnest: [{ list: "price.price_specifications", alias: "price_spec" }],
  filters: ["product.item_type = 'BOOK'", "price_spec.currency_code = 'GBP'"],
  mappings: [
    { type: "one_to_one", source: "product.isbn13", target: "isbn13" },
    { type: "one_to_one", source: "product.title", target: "title" },
    { type: "derived", expression: "round(price_spec.amount, 2)", target: "list_price" },
    { type: "literal", value: "retail", target: "channel" },
    { type: "one_to_many", source: "product.dims", targets: ["w", "h"], expressions: ["product.dims.w", "product.dims.h"] },
  ],
};
const REL = [{ driving: "product", related: "price", row: "one row per price" }];

describe("recipeSources", () => {
  it("puts the driving table first, then each join with its type and condition", () => {
    expect(recipeSources(MC)).toEqual([
      { alias: "product", table: "uif_echo_dev_gold_product.product", driving: true, join: null },
      { alias: "price", table: "uif_echo_dev_gold_price.price", driving: false,
        join: { type: "left", on: "product.product_id = price.product_id", through: null } },
    ]);
  });

  it("names the list a join goes through, and reads left_semi as words", () => {
    const mc = { sources: [{ name: "product", path: "a.b.product" }, { name: "program", path: "a.b.program" }],
      joins: [{ left: "product", right: "program", type: "left_semi", condition: "x", through: { list: "product.associated_programs", alias: "pp" } }] };
    expect(recipeSources(mc)[1].join).toEqual({ type: "left semi", on: "x", through: "product.associated_programs" });
  });

  it("tolerates a source keyed by alias and an empty config", () => {
    expect(recipeSources({ sources: [{ alias: "product", path: "x.y.product" }] })[0].alias).toBe("product");
    expect(recipeSources(undefined)).toEqual([]);
  });
});

describe("recipeSpreads", () => {
  it("lists what is spread into one row per element", () => {
    expect(recipeSpreads(MC)).toEqual([{ list: "price.price_specifications", alias: "price_spec" }]);
  });
});

describe("recipeFilters", () => {
  it("shows published Spark SQL verbatim when there is no matching builder model", () => {
    expect(recipeFilters(MC, {})).toEqual([
      { kind: "sql", text: "product.item_type = 'BOOK'" },
      { kind: "sql", text: "price_spec.currency_code = 'GBP'" },
    ]);
  });

  it("is empty when every row is exported", () => {
    expect(recipeFilters({ filters: [] }, {})).toEqual([]);
  });
});

describe("recipeFields", () => {
  it("lists every field in file order with where it comes from and its kind", () => {
    expect(recipeFields(MC)).toEqual([
      { n: 1, target: "isbn13", from: "product.isbn13", kind: "column" },
      { n: 2, target: "title", from: "product.title", kind: "column" },
      { n: 3, target: "list_price", from: "round(price_spec.amount, 2)", kind: "expression" },
      { n: 4, target: "channel", from: "'retail'", kind: "fixed" },
      { n: 5, target: "w", from: "product.dims.w", kind: "expression" },
      { n: 6, target: "h", from: "product.dims.h", kind: "expression" },
    ]);
  });

  it("reads a very old target → source object too", () => {
    expect(recipeFields({ mapping: { isbn13: "product.isbn13" } })).toEqual([
      { n: 1, target: "isbn13", from: "product.isbn13", kind: "column" },
    ]);
  });

  it("shows a number literal without quotes", () => {
    expect(recipeFields({ mappings: [{ type: "literal", value: 0, target: "zero" }] })[0].from).toBe("0");
  });
});

describe("fieldKindsText", () => {
  it("names only the kinds present", () => {
    expect(fieldKindsText(recipeFields(MC))).toBe("2 columns · 3 expressions · 1 fixed");
    expect(fieldKindsText([{ kind: "column" }])).toBe("1 column");
  });
});

describe("recipeGrain / recipeSentence", () => {
  it("uses the declared grain of the last declared join", () => {
    expect(recipeGrain(MC, REL)).toBe("one row per price");
    expect(recipeGrain(MC, [])).toBe("");
  });

  it("builds the sentence from the version only: grain, short table name, field count, format", () => {
    const v = { mapping_config: MC, settings: { formats: ["csv"] } };
    expect(recipeSentence(v, REL)).toBe("One row per price of product, sent as 6 fields in a CSV file.");
  });

  it("never mentions the filter conditions (UIFPE-642) and reads a legacy settings.format", () => {
    const v = { mapping_config: { sources: [{ name: "product", path: "a.b.product" }], filters: ["x = 1", "y = 2"],
      mappings: [{ type: "one_to_one", source: "product.a", target: "a" }] },
      settings: { format: "json", filter_model: { version: 1, clauses: [] } } };
    expect(recipeSentence(v, [])).toBe("Rows of product, sent as 1 field in a JSON file.");
  });

  it("without a declared grain it names the joined tables, with 'an' before XML", () => {
    const v = { mapping_config: { ...MC, filters: [] }, settings: { formats: ["xml"] } };
    expect(recipeSentence(v, [])).toBe("Rows of product joined with price, sent as 6 fields in an XML file.");
  });
});

describe("lastRunFigures", () => {
  it("reads the run's counts", () => {
    const run = { status: "succeeded", recorded_at: "t", summary: { output_rows: 48312, rejected_rows: 0, filtered_rows: 10 } };
    expect(lastRunFigures(run, () => 3)).toEqual({ status: "succeeded", at: "t", delivered: 48312, rejected: 0, filtered: 10, files: 3 });
  });

  it("says not measured (null) for counts an older run never recorded — never 0", () => {
    expect(lastRunFigures({ status: "succeeded", summary: null })).toMatchObject({ delivered: null, rejected: null, filtered: null });
    expect(lastRunFigures(null)).toBeNull();
  });
});

describe("searchFields", () => {
  it("matches field names and sources, ignoring case", () => {
    const f = recipeFields(MC);
    expect(searchFields(f, "PRICE").map((x) => x.target)).toEqual(["list_price"]);
    expect(searchFields(f, "product.t").map((x) => x.target)).toEqual(["title"]);
    expect(searchFields(f, "")).toBe(f);
  });
});
