// relationships.test.js — UIFPE-581: the declared Gold relationships prefill
// the join when a table is added to a feed, a join through a list column
// carries its `through` step into the config, and a spread list's element
// fields are offered on the Filter and Mapping steps like a source's columns.
import { describe, expect, it } from "vitest";

import {
  buildMappingConfig,
  columnMapped,
  describeJoin,
  elementAliasesOf,
  elementFields,
  joinFromConfig,
  joinLeftAlias,
  joinsComplete,
  listColumnsFor,
  mappingForColumn,
  narrowHint,
  normalizeFeedSources,
  relationshipFor,
  rowShape,
  tableHeading,
  tableOf,
} from "../feedSources.js";
import { attributeColumnsFor, filterContext, suggestionsEligible } from "../rowFilter.js";

const tbl = (domain, table) => ({
  id: `${domain}.${table}`,
  path: `glue_catalog.uif_echo_dev_gold_${domain}.${table}`,
  kind: "iceberg",
  domain,
});
const PRODUCT = tbl("product", "product");
const COPY = tbl("product", "marketing_copy");
const PROGRAM = tbl("program", "program");
const STOCK = tbl("stock", "stock");
const PRICE = tbl("price", "price");
const entry = (source, alias, join = null) => ({ source, alias, join });

// GET /registries/relationships as registry_data/relationships.yaml declares it
const RELATIONSHIPS = [
  {
    driving: "product", related: "price", summary: "matched on product id; one row per price", row: "one row per price",
    join: { type: "left", condition: "{product}.product_id = {price}.product_id" },
    unnest: [{ list: "{price}.price_specifications", alias: "price_spec" }],
    narrow_by: ["price_spec.price_list_name"],
  },
  {
    driving: "product", related: "stock", summary: "matched on product id and org code; one row per org", row: "one row per org",
    join: { type: "left", condition: "{product}.product_id = {stock}.product_id AND {product}.organization_code = {stock}.organization_code" },
    narrow_by: ["{stock}.organization_code"],
  },
  {
    driving: "product", related: "program", summary: "through each product's associated programs, matched on program id", row: "one row per program",
    join: { type: "left", through: { list: "{product}.associated_programs", alias: "product_program" },
            condition: "product_program.value = {program}.program_id" },
  },
  {
    driving: "product", related: "marketing_copy", summary: "through each product's marketing copy list, matched on content id", row: "one row per copy",
    join: { type: "left", through: { list: "{product}.marketing_copy", alias: "product_copy" },
            condition: "product_copy.content_id = {marketing_copy}.content_id" },
    narrow_by: ["product_copy.owning_market_code", "product_copy.audience_code"],
  },
];

const ATTRS = {
  [PRODUCT.id]: {
    provenance: "live",
    columns: ["product_id", "organization_code", "associated_programs", "marketing_copy"],
    types: {
      product_id: "string", organization_code: "string", associated_programs: "array<string>",
      marketing_copy: "array<struct<content_id:string,audience_code:string,owning_market_code:string,marketing_copy_text:string>>",
    },
  },
  [COPY.id]: { provenance: "live", columns: ["content_id", "marketing_copy_content_text"],
               types: { content_id: "string", marketing_copy_content_text: "string" } },
  [PRICE.id]: { provenance: "live", columns: ["product_id", "price_specifications"],
                types: { product_id: "string",
                         price_specifications: "array<struct<price_list_name:string,list_price_amount:decimal(38, 2)>>" } },
};

describe("tableOf / relationshipFor", () => {
  it("names the Gold table from any source id shape", () => {
    expect(tableOf("product.product")).toBe("product");
    expect(tableOf("gold_product.product")).toBe("product");
    expect(tableOf("product.marketing_copy")).toBe("marketing_copy");
  });
  it("substitutes the feed's aliases into the declared join, through and unnest", () => {
    const driving = entry(PRODUCT, "p");
    const copy = relationshipFor(RELATIONSHIPS, driving, entry(COPY, "mc"));
    expect(copy.type).toBe("left");
    expect(copy.condition).toBe("product_copy.content_id = mc.content_id");
    expect(copy.through).toEqual({ list: "p.marketing_copy", alias: "product_copy" });
    expect(copy.relationship.summary).toMatch(/marketing copy list/);
    expect(copy.auto).toBe(true);
    const price = relationshipFor(RELATIONSHIPS, driving, entry(PRICE, "pr"));
    expect(price.condition).toBe("p.product_id = pr.product_id");
    expect(price.unnest).toEqual([{ list: "pr.price_specifications", alias: "price_spec" }]);
    expect(price.relationship.narrow_by).toEqual(["price_spec.price_list_name"]);
    const stock = relationshipFor(RELATIONSHIPS, driving, entry(STOCK, "s"));
    expect(stock.condition).toBe("p.product_id = s.product_id AND p.organization_code = s.organization_code");
    expect(stock.relationship.narrow_by).toEqual(["s.organization_code"]);
  });
  it("is null for a pair nobody declared, and when the driving table is not the declared one", () => {
    expect(relationshipFor(RELATIONSHIPS, entry(PRODUCT, "p"), entry(tbl("x", "ghost"), "g"))).toBeNull();
    expect(relationshipFor(RELATIONSHIPS, entry(STOCK, "s"), entry(PRODUCT, "p"))).toBeNull();
    expect(relationshipFor([], entry(PRODUCT, "p"), entry(STOCK, "s"))).toBeNull();
  });
});

describe("normalizeFeedSources with relationships", () => {
  it("prefills the declared join for a newly added table", () => {
    const [, copy] = normalizeFeedSources([entry(PRODUCT, "product"), entry(COPY, "marketing_copy")], ATTRS, RELATIONSHIPS);
    expect(copy.join.relationship.summary).toMatch(/content id/);
    expect(copy.join.through).toEqual({ list: "product.marketing_copy", alias: "product_copy" });
    expect(joinsComplete([entry(PRODUCT, "product"), copy])).toBe(true);
  });
  it("upgrades a wizard-built default join once the relationships land, but never an author's edit", () => {
    const before = normalizeFeedSources([entry(PRODUCT, "product"), entry(STOCK, "stock")], ATTRS, []);
    expect(before[1].join.auto).toBe(true);
    expect(before[1].join.relationship).toBeUndefined();
    const after = normalizeFeedSources(before, ATTRS, RELATIONSHIPS);
    expect(after[1].join.relationship.summary).toMatch(/org code/);
    const edited = [entry(PRODUCT, "product"), entry(STOCK, "stock", { type: "inner", pairs: [{ leftRef: "product.product_id", rightColumn: "product_id" }], condition: null, auto: false })];
    const kept = normalizeFeedSources(edited, ATTRS, RELATIONSHIPS)[1].join;
    expect(kept).toEqual(edited[1].join);
    expect(kept.relationship).toBeUndefined();
  });
  it("does not reset a through-list join for referencing its own element alias", () => {
    const entries = normalizeFeedSources([entry(PRODUCT, "product"), entry(COPY, "marketing_copy")], ATTRS, RELATIONSHIPS);
    const again = normalizeFeedSources(entries, ATTRS, RELATIONSHIPS);
    expect(again[1].join.condition).toBe("product_copy.content_id = marketing_copy.content_id");
    expect(again[1].join.through).toBeTruthy();
  });
  it("falls back to the first shared column when nothing is declared", () => {
    const [, price] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PRICE, "price")], ATTRS, []);
    expect(price.join.pairs).toEqual([{ leftRef: "product.product_id", rightColumn: "product_id" }]);
  });
});

describe("the config a relationship feed becomes", () => {
  const feed = normalizeFeedSources(
    [entry(PRODUCT, "product"), entry(COPY, "marketing_copy"), entry(PRICE, "price")], ATTRS, RELATIONSHIPS);
  const config = buildMappingConfig({ feedSources: feed, mappings: [], filters: ["product_copy.audience_code = 'STU'"] });
  it("carries through on the list join and unnest at the top level", () => {
    expect(config.joins[0]).toEqual({
      left: "product", right: "marketing_copy", type: "left",
      condition: "product_copy.content_id = marketing_copy.content_id",
      through: { list: "product.marketing_copy", alias: "product_copy" },
    });
    expect(config.joins[1]).toEqual({ left: "product", right: "price", type: "left", condition: "product.product_id = price.product_id" });
    expect(config.unnest).toEqual([{ list: "price.price_specifications", alias: "price_spec" }]);
  });
  it("attaches a through-list join to the driving source", () => {
    expect(joinLeftAlias(feed[1], [feed[0]])).toBe("product");
  });
  it("summarises the join in the relationship's words", () => {
    expect(describeJoin(feed[1])).toBe("marketing_copy · left join, through each product's marketing copy list, matched on content id");
  });
  it("round-trips a stored through join and its unnest steps for editing", () => {
    const j = joinFromConfig(config.joins[0], config.unnest);
    expect(j).toEqual({ type: "left", pairs: [], condition: "product_copy.content_id = marketing_copy.content_id",
                        through: { list: "product.marketing_copy", alias: "product_copy" }, unnest: [] });
    expect(joinFromConfig(config.joins[1], config.unnest).unnest).toEqual([{ list: "price.price_specifications", alias: "price_spec" }]);
  });
  it("leaves a config without list steps exactly as before", () => {
    const plain = buildMappingConfig({ feedSources: normalizeFeedSources([entry(PRODUCT, "product"), entry(PRICE, "price")], ATTRS, []), mappings: [] });
    expect(plain.joins[0].through).toBeUndefined();
    expect("unnest" in plain).toBe(false);
  });
});

describe("elementFields / elementAliasesOf / filterContext with list elements", () => {
  it("reads the element fields and types from a Glue array type", () => {
    expect(elementFields("array<struct<content_id:string,list_price_amount:decimal(38, 2),tiers:array<struct<a:int,b:int>>>>")).toEqual([
      { name: "content_id", type: "string" }, { name: "list_price_amount", type: "decimal(38, 2)" },
      { name: "tiers", type: "array<struct<a:int,b:int>>" }]);
    expect(elementFields("array<string>")).toEqual([{ name: "value", type: "string" }]);
    expect(elementFields("string")).toBeNull();
    expect(elementFields(undefined)).toBeNull();
  });
  const feed = normalizeFeedSources(
    [entry(PRODUCT, "product"), entry(COPY, "marketing_copy"), entry(PROGRAM, "program"), entry(PRICE, "price")], ATTRS, RELATIONSHIPS);
  it("lists every spread in engine order with where the list lives and the table it belongs to", () => {
    expect(elementAliasesOf(feed).map((e) => [e.alias, e.list, e.sourceId, e.tableAlias])).toEqual([
      ["product_copy", "product.marketing_copy", PRODUCT.id, "marketing_copy"],
      ["product_program", "product.associated_programs", PRODUCT.id, "program"],
      ["price_spec", "price.price_specifications", PRICE.id, "price"],
    ]);
  });
  // UIFPE-592: what a spread list ADDS to its table — the fields the table
  // does not already have; a lone `value` (a list of ids) adds nothing
  it("folds a spread list into its table: duplicates and a lone value are dropped", () => {
    const [copy, program, price] = elementAliasesOf(feed);
    expect(listColumnsFor(copy, feed, ATTRS).map((f) => f.name)).toEqual(["audience_code", "owning_market_code", "marketing_copy_text"]);
    expect(listColumnsFor(program, feed, ATTRS)).toEqual([]);
    expect(listColumnsFor(price, feed, ATTRS).map((f) => f.name)).toEqual(["price_list_name", "list_price_amount"]);
    expect(listColumnsFor(copy, feed, {})).toBeNull(); // types not loaded yet
  });
  it("heads a card by what a row is, never by the engine's alias", () => {
    expect(tableHeading(feed[1])).toBe("Marketing copy · one row per copy");
    expect(tableHeading(feed[0])).toBe(`Source attributes · ${PRODUCT.id}`);
  });
  // UIFPE-609: a list/record chip in a CSV export maps as to_json(...); JSON
  // and XML keep the bare column; a wrapped column still reads as mapped
  it("wraps a list column in to_json for csv only, and knows it is mapped either way", () => {
    const listType = "array<struct<content_id:string>>";
    expect(mappingForColumn("product", "marketing_copy", listType, "csv", "marketing_copy")).toEqual({
      type: "derived", expression: "to_json(product.marketing_copy)", target: "marketing_copy",
    });
    expect(mappingForColumn("product", "marketing_copy", listType, "json", "marketing_copy")).toEqual({
      type: "one_to_one", source: "product.marketing_copy", target: "marketing_copy",
    });
    expect(mappingForColumn("product", "title", "string", "csv", "title")).toEqual({
      type: "one_to_one", source: "product.title", target: "title",
    });
    expect(columnMapped([{ type: "derived", expression: "to_json(product.marketing_copy)" }], "product", "marketing_copy")).toBe(true);
    expect(columnMapped([{ type: "one_to_one", source: "product.title" }], "product", "title")).toBe(true);
    expect(columnMapped([{ type: "one_to_one", source: "product.title" }], "product", "isbn13")).toBe(false);
  });
  // UIFPE-607: the row-shape sentence and the narrowing hint come from the
  // declared labels only — the driving table's registry `row`/`narrow_by`
  // and each relationship join's `row`
  it("says what a row is from the declared labels, and nothing when none are declared", () => {
    const declared = [
      { ...feed[0], source: { ...PRODUCT, row: "one row per product per organization", narrow_by: ["organization_code"] } },
      ...feed.slice(1),
    ];
    expect(rowShape(declared)).toBe("Each row is one product per organization, per copy, per program, per price.");
    expect(narrowHint(declared)).toBe("To keep fewer rows, narrow by product.organization_code.");
    expect(filterContext(declared, ATTRS).rowNote).toBe(
      "Each row is one product per organization, per copy, per program, per price. " +
        "To keep fewer rows, narrow by product.organization_code."
    );
    expect(rowShape(feed)).toBe("");   // PRODUCT declares no row
    expect(narrowHint(feed)).toBe("");
    expect(filterContext(feed, ATTRS).rowNote).toBe("");
  });
  const ctx = filterContext(feed, ATTRS);
  it("adds only the element aliases that add fields, grouped under their table", () => {
    expect(ctx.elementAliases).toEqual(["product_copy", "price_spec"]);   // program's id list adds nothing
    expect(ctx.aliases).toEqual(["product", "marketing_copy", "program", "price", "product_copy", "price_spec"]);
    expect(ctx.columnsByAlias.product_copy).toEqual(["audience_code", "owning_market_code", "marketing_copy_text"]);
    expect(ctx.typesByAlias.price_spec).toEqual({ price_list_name: "string", list_price_amount: "decimal(38, 2)" });
    expect(ctx.groupOfAlias).toEqual({ product: "product", marketing_copy: "marketing_copy", program: "program", price: "price",
                                       product_copy: "marketing_copy", price_spec: "price" });
    expect(ctx.groupLabelByAlias.marketing_copy).toBe("Marketing copy · one row per copy");
    expect(ctx.groupLabelByAlias.product).toBe(`product · ${PRODUCT.id}`);
    expect(ctx.sourceIdByAlias.product_copy).toBe("from product.marketing_copy");
    expect(ctx.primaryAlias).toBe("product");
  });
  it("offers a spread element's text fields without waiting for a profile", () => {
    const cols = attributeColumnsFor(ctx, {});
    expect(cols.offered.product_copy).toEqual(["audience_code", "owning_market_code", "marketing_copy_text"]);
    expect(cols.hidden.product_copy).toEqual([]);
    expect(cols.pending.product_copy).toEqual([]);
    expect(cols.offered.price_spec).toEqual(["price_list_name", "list_price_amount"]);
  });
  it("does not ask the API for value suggestions inside a list", () => {
    const clause = { kind: "attribute", column: "product_copy.audience_code", category: "string", op: "eq", value: "" };
    expect(suggestionsEligible(clause, ctx, {})).toEqual({ eligible: false, reason: "values inside a list are not suggested — type the value" });
  });
});
