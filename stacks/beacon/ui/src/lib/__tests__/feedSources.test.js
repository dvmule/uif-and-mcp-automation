// feedSources.test.js — the wizard's multi-source feed rules (UIFPE-237 /
// UIFPE-167), pinned without a browser. These rules decide what the engine
// is asked to run: which tables, attached where, on which condition. Two
// regressions are pinned by name: the hard-coded gold_product.product
// fallback that used to appear in a config when nothing was selected, and
// the join TYPE that used to reset to "left" whenever a source was toggled.
import { describe, expect, it } from "vitest";

import {
  JOIN_TYPES,
  buildMappingConfig,
  deriveAlias,
  describeJoin,
  explainJoin,
  hintDomain,
  joinConditionOf,
  joinFromConfig,
  joinComplete,
  joinLeftAlias,
  joinRuleLabel,
  joinsComplete,
  knownColumns,
  normalizeFeedSources,
  parseEqualityJoins,
  referencedQualifiers,
  resolveConvertedSources,
  unmapColumn,
} from "../feedSources.js";

// catalog table records as GET /registries/domains/{d}/tables returns them —
// no `columns` list; introspection is the only column source
const tbl = (domain, table) => ({
  id: `${domain}.${table}`,
  path: `glue_catalog.echo_dev_gold_${domain}.${table}`,
  kind: "iceberg",
  domain,
});
const PRODUCT = tbl("product", "product");
const PRICE = tbl("price", "price");
const PROGRAM = tbl("program", "program");
const STOCK = tbl("stock", "stock");
const PRODUCT_PRICE = tbl("product", "price"); // a same-named table in another domain

const live = (columns) => ({ columns, provenance: "live" });
const ATTRS = {
  [PRODUCT.id]: live(["ppid", "isbn13", "description"]),
  [PRICE.id]: live(["ppid", "isbn13", "priceSpecifications"]),
  [PROGRAM.id]: live(["programId", "name", "ppid"]),
  [STOCK.id]: live(["ppid", "organizationCode", "qtyOnHand"]),
};

const entry = (source, alias, join = null) => ({ source, alias, join });
const pairs = (leftRef, rightColumn, type = "left") => ({
  type,
  pairs: [{ leftRef, rightColumn }],
  condition: null,
});
const custom = (condition, type = "left") => ({ type, pairs: [], condition });

describe("deriveAlias", () => {
  it("takes the last id segment, lowercased and sanitized", () => {
    expect(deriveAlias("price.price")).toBe("price");
    expect(deriveAlias("gold_product.Marketing-Copy")).toBe("marketingcopy");
  });
  it("suffixes collisions and falls back to src", () => {
    expect(deriveAlias("price.price", ["price"])).toBe("price_2");
    expect(deriveAlias("price.price", ["price", "price_2"])).toBe("price_3");
    expect(deriveAlias("x.---")).toBe("src");
  });
});

describe("knownColumns", () => {
  it("prefers live columns, then the record's static list, then nothing", () => {
    expect(knownColumns(PRICE, ATTRS)).toEqual(["ppid", "isbn13", "priceSpecifications"]);
    expect(knownColumns({ ...PRICE, columns: ["a"] }, {})).toEqual(["a"]);
    expect(knownColumns(PRICE, {})).toEqual([]);
  });
});

describe("parseEqualityJoins ∘ joinConditionOf", () => {
  it("round-trips one pair and several ANDed pairs", () => {
    const one = entry(PRICE, "price", pairs("product.ppid", "ppid"));
    expect(joinConditionOf(one)).toBe("product.ppid = price.ppid");
    expect(parseEqualityJoins(joinConditionOf(one), "price")).toEqual(one.join.pairs);

    const many = entry(STOCK, "stock", {
      type: "inner",
      pairs: [
        { leftRef: "product.ppid", rightColumn: "ppid" },
        { leftRef: "product.isbn13", rightColumn: "isbn13" },
        { leftRef: "price.ppid", rightColumn: "ppid" },
      ],
      condition: null,
    });
    expect(joinConditionOf(many)).toBe(
      "product.ppid = stock.ppid AND product.isbn13 = stock.isbn13 AND price.ppid = stock.ppid"
    );
    expect(parseEqualityJoins(joinConditionOf(many), "stock")).toEqual(many.join.pairs);
  });
  it("normalises a reversed clause and refuses anything without a dropdown shape", () => {
    expect(parseEqualityJoins("price.ppid = product.ppid", "price")).toEqual([
      { leftRef: "product.ppid", rightColumn: "ppid" },
    ]);
    expect(parseEqualityJoins("price.a = price.b", "price")).toBeNull();
    expect(parseEqualityJoins("product.ppid = price.ppid AND price.vatCountry = 'GB'", "price")).toBeNull();
    expect(parseEqualityJoins("product.ppid > price.ppid", "price")).toBeNull();
    expect(parseEqualityJoins("", "price")).toBeNull();
    expect(parseEqualityJoins(undefined, "price")).toBeNull();
  });
});

describe("joinConditionOf", () => {
  it("emits custom SQL verbatim and skips incomplete pairs", () => {
    expect(joinConditionOf(entry(PRICE, "price", custom("product.ppid = price.ppid AND price.vatCountry = 'GB'")))).toBe(
      "product.ppid = price.ppid AND price.vatCountry = 'GB'"
    );
    const half = entry(PRICE, "price", {
      type: "left",
      pairs: [{ leftRef: "product.ppid", rightColumn: "ppid" }, { leftRef: "", rightColumn: "" }],
      condition: null,
    });
    expect(joinConditionOf(half)).toBe("product.ppid = price.ppid");
  });
  it("is empty for a cross join even when pairs or a condition are still on the entry", () => {
    expect(joinConditionOf(entry(PRICE, "price", pairs("product.ppid", "ppid", "cross")))).toBe("");
    expect(joinConditionOf(entry(PRICE, "price", custom("product.ppid = price.ppid", "cross")))).toBe("");
    expect(joinConditionOf(entry(PRODUCT, "product"))).toBe("");
  });
});

describe("normalizeFeedSources", () => {
  it("strips the driving entry's join and default-builds missing joins on a shared column", () => {
    const out = normalizeFeedSources(
      [entry(PRODUCT, "product", pairs("x.y", "z")), entry(PRICE, "price")],
      ATTRS
    );
    expect(out[0].join).toBeNull();
    // UIFPE-581: a wizard-built join is marked auto so the declared
    // relationships can upgrade it when they land
    expect(out[1].join).toEqual({ ...pairs("product.ppid", "ppid"), auto: true });
  });
  it("prefers ppid, else the first shared column, else one blank pair", () => {
    const [, program] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PROGRAM, "program")], ATTRS);
    expect(program.join.pairs).toEqual([{ leftRef: "product.ppid", rightColumn: "ppid" }]);
    const attrsNoPpid = { ...ATTRS, [PROGRAM.id]: live(["programId", "isbn13"]) };
    const [, byIsbn] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PROGRAM, "program")], attrsNoPpid);
    expect(byIsbn.join.pairs).toEqual([{ leftRef: "product.isbn13", rightColumn: "isbn13" }]);
    const [, nothing] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PROGRAM, "program")], {});
    expect(nothing.join.pairs).toEqual([{ leftRef: "", rightColumn: "" }]);
  });
  it("keeps the join TYPE when a custom condition references a deselected alias (regression)", () => {
    // before UIFPE-167 this path reset the type to "left" — toggling an
    // unrelated source off silently turned an inner join into a left join
    const [, price] = normalizeFeedSources(
      [entry(PRODUCT, "product"), entry(PRICE, "price", custom("gone.ppid = price.ppid", "inner"))],
      ATTRS
    );
    expect(price.join).toEqual(pairs("product.ppid", "ppid", "inner"));
  });
  it("resets a custom condition that references a source joined LATER", () => {
    const [, price, stock] = normalizeFeedSources(
      [
        entry(PRODUCT, "product"),
        entry(PRICE, "price", custom("stock.ppid = price.ppid")),
        entry(STOCK, "stock", pairs("product.ppid", "ppid")),
      ],
      ATTRS
    );
    expect(price.join.condition).toBeNull();
    expect(price.join.pairs).toEqual([{ leftRef: "product.ppid", rightColumn: "ppid" }]);
    expect(stock.join).toEqual(pairs("product.ppid", "ppid"));
  });
  it("keeps a custom condition that only references visible aliases", () => {
    const join = custom("product.ppid = price.ppid AND price.vatCountry = 'GB'");
    const [, price] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PRICE, "price", join)], ATTRS);
    expect(price.join).toEqual(join);
  });
  it("blanks a pair whose left alias is no longer prior, keeps the others", () => {
    const [, , stock] = normalizeFeedSources(
      [
        entry(PRODUCT, "product"),
        entry(PRICE, "price", pairs("product.ppid", "ppid")),
        entry(STOCK, "stock", {
          type: "full",
          pairs: [
            { leftRef: "program.ppid", rightColumn: "ppid" },
            { leftRef: "price.ppid", rightColumn: "ppid" },
          ],
          condition: null,
        }),
      ],
      ATTRS
    );
    expect(stock.join.type).toBe("full");
    expect(stock.join.pairs).toEqual([
      { leftRef: "", rightColumn: "" },
      { leftRef: "price.ppid", rightColumn: "ppid" },
    ]);
  });
  it("leaves a cross join and a single entry untouched", () => {
    const cross = { type: "cross", pairs: [], condition: null };
    const [, price] = normalizeFeedSources([entry(PRODUCT, "product"), entry(PRICE, "price", cross)], ATTRS);
    expect(price.join).toEqual(cross);
    const single = [entry(PRODUCT, "product")];
    expect(normalizeFeedSources(single, ATTRS)).toEqual(single);
    expect(normalizeFeedSources([], ATTRS)).toEqual([]);
  });
});

describe("joinComplete / joinRuleLabel (UIFPE-717)", () => {
  it("checks one joined entry the way joinsComplete checks them all", () => {
    expect(joinComplete(entry(PRICE, "price", pairs("product.ppid", "ppid")))).toBe(true);
    expect(joinComplete(entry(PRICE, "price", pairs("", "")))).toBe(false);
    expect(joinComplete(entry(PRICE, "price", custom("  ")))).toBe(false);
    expect(joinComplete(entry(PRICE, "price", { type: "cross", pairs: [], condition: null }))).toBe(true);
  });
  it("labels a join by its position and table", () => {
    expect(joinRuleLabel(entry(PRICE, "price"), 1)).toBe("Join 1 · price");
    expect(joinRuleLabel(entry(PRODUCT_PRICE, "price_2"), 2)).toBe("Join 2 · price");
  });
});

describe("joinsComplete", () => {
  const driving = entry(PRODUCT, "product");
  it("is true with no joins to complete", () => {
    expect(joinsComplete([])).toBe(true);
    expect(joinsComplete([driving])).toBe(true);
  });
  it("needs every pair filled, or non-blank custom SQL, per joined source", () => {
    expect(joinsComplete([driving, entry(PRICE, "price", pairs("product.ppid", "ppid"))])).toBe(true);
    expect(joinsComplete([driving, entry(PRICE, "price", pairs("", ""))])).toBe(false);
    expect(
      joinsComplete([
        driving,
        entry(PRICE, "price", {
          type: "full",
          pairs: [{ leftRef: "product.ppid", rightColumn: "ppid" }, { leftRef: "", rightColumn: "" }],
          condition: null,
        }),
      ])
    ).toBe(false);
    expect(joinsComplete([driving, entry(PRICE, "price", custom("  "))])).toBe(false);
    expect(joinsComplete([driving, entry(PRICE, "price", custom("product.ppid = price.ppid"))])).toBe(true);
    expect(joinsComplete([driving, entry(PRICE, "price", { type: "cross", pairs: [], condition: null })])).toBe(true);
    expect(
      joinsComplete([
        driving,
        entry(PRICE, "price", pairs("product.ppid", "ppid")),
        entry(STOCK, "stock", pairs("product.ppid", "")),
      ])
    ).toBe(false);
  });
});

describe("joinFromConfig", () => {
  it("turns an equality condition into pairs and keeps everything else as custom SQL", () => {
    expect(joinFromConfig({ left: "product", right: "price", type: "left", condition: "product.ppid = price.ppid" })).toEqual(
      pairs("product.ppid", "ppid")
    );
    expect(joinFromConfig({ left: "product", right: "price", type: "right", condition: "product.ppid = price.ppid AND price.x = 'y'" })).toEqual(
      custom("product.ppid = price.ppid AND price.x = 'y'", "right")
    );
  });
  it("uses the engine's default type and a null condition for cross joins", () => {
    // config_loader.parse_config defaults a missing type to inner — the
    // wizard must not silently turn a stored inner join into a left join
    expect(joinFromConfig({ left: "a", right: "b", condition: "a.id = b.id" }).type).toBe("inner");
    expect(joinFromConfig({ left: "a", right: "b", type: "cross" })).toEqual({ type: "cross", pairs: [], condition: null });
    expect(joinFromConfig({ left: "a", right: "b", type: "cross", condition: "" })).toEqual({ type: "cross", pairs: [], condition: null });
  });
});

describe("referencedQualifiers / joinLeftAlias", () => {
  it("lists dotted qualifiers once, in order, ignoring literals and function names", () => {
    expect(referencedQualifiers("lower(price.vatCountry) = 'a.b' AND product.ppid = price.ppid")).toEqual(["price", "product"]);
    expect(referencedQualifiers("")).toEqual([]);
  });
  const product = entry(PRODUCT, "product");
  const price = entry(PRICE, "price", pairs("product.ppid", "ppid"));
  it("is null for the driving entry and the driving alias for entries[1]", () => {
    expect(joinLeftAlias(product, [])).toBeNull();
    expect(joinLeftAlias(price, [product])).toBe("product");
  });
  it("attaches to the ONE prior alias the pairs reference, else the driver", () => {
    expect(joinLeftAlias(entry(STOCK, "stock", pairs("price.ppid", "ppid")), [product, price])).toBe("price");
    expect(
      joinLeftAlias(
        entry(STOCK, "stock", {
          type: "left",
          pairs: [{ leftRef: "product.ppid", rightColumn: "ppid" }, { leftRef: "price.isbn13", rightColumn: "isbn13" }],
          condition: null,
        }),
        [product, price]
      )
    ).toBe("product");
  });
  it("reads a custom condition the same way, ignoring its own alias", () => {
    expect(joinLeftAlias(entry(STOCK, "stock", custom("price.ppid = stock.ppid AND stock.qtyOnHand > 0")), [product, price])).toBe("price");
    expect(joinLeftAlias(entry(STOCK, "stock", custom("price.ppid = stock.ppid AND product.ppid = stock.ppid")), [product, price])).toBe("product");
    expect(joinLeftAlias(entry(STOCK, "stock", custom("stock.qtyOnHand > 0")), [product, price])).toBe("product");
  });
  it("attaches a cross join to the driver", () => {
    expect(joinLeftAlias(entry(STOCK, "stock", { type: "cross", pairs: [], condition: null }), [product, price])).toBe("product");
  });
});

describe("hintDomain", () => {
  it("normalises any database-ish segment to the platform's domain name", () => {
    for (const s of ["echo_dev_gold_price", "gold_price", "price", "GOLD_PRICE", "eds_dev_gold_price"]) {
      expect(hintDomain(s)).toBe("price");
    }
    expect(hintDomain("eds_dev_gold_product")).toBe("product");
  });
});

describe("resolveConvertedSources", () => {
  const POOL = [PRODUCT, PRICE, PROGRAM, PRODUCT_PRICE];
  const conv = (sources, joins = []) => ({ sources, joins });
  const src = (name, hint) => ({ name, registry_hint: hint, format: "iceberg" });

  it("resolves exact ids, gold_-prefixed, env-prefixed and catalog-qualified hints", () => {
    for (const hint of ["price.price", "gold_price.price", "echo_dev_gold_price.price", "glue_catalog.echo_dev_gold_price.price", "GOLD_PRICE.PRICE"]) {
      const { entries, unmatched } = resolveConvertedSources(conv([src("pr", hint)]), POOL);
      expect(unmatched, hint).toEqual([]);
      expect(entries[0].source, hint).toBe(PRICE);
      expect(entries[0].alias).toBe("pr");
    }
  });
  it("resolves a bare table name when it is unique and flags it when it is not", () => {
    expect(resolveConvertedSources(conv([src("g", "program")]), POOL).entries[0].source).toBe(PROGRAM);
    const { entries, unmatched } = resolveConvertedSources(conv([src("pr", "price")]), POOL);
    expect(entries).toEqual([]);
    expect(unmatched).toEqual([
      { hint: "price", alias: "pr", reason: "ambiguous", candidates: ["price.price", "product.price"] },
    ]);
  });
  it("flags an unknown table without touching the others", () => {
    const { entries, unmatched } = resolveConvertedSources(
      conv([src("n", "nope.nope"), src("pr", "price.price")], [{ left: "n", right: "pr", type: "left", condition: "n.ppid = pr.ppid" }]),
      POOL
    );
    expect(unmatched).toEqual([{ hint: "nope.nope", alias: "n", reason: "unknown", candidates: [] }]);
    // the matched JOIN table is still reported (the caller decides whether a
    // partial result may be applied — the wizard does not)
    expect(entries.map((e) => e.alias)).toEqual(["pr"]);
    expect(entries[0].join).toBeNull(); // entries[0] never carries a join
  });
  it("attaches converted joins by right alias, never to the first entry", () => {
    const sample = conv(
      [src("p", "gold_product.product"), src("pr", "gold_price.price")],
      [{ left: "p", right: "pr", type: "left", condition: "p.ppid = pr.ppid" }]
    );
    const { entries, unmatched } = resolveConvertedSources(sample, POOL);
    expect(unmatched).toEqual([]);
    expect(entries.map((e) => e.source)).toEqual([PRODUCT, PRICE]);
    expect(entries[0].join).toBeNull();
    expect(entries[1].join).toEqual(pairs("p.ppid", "ppid"));
  });
  it("keeps a non-equality converted join as custom SQL with its type", () => {
    const { entries } = resolveConvertedSources(
      conv(
        [src("p", "product.product"), src("pr", "price.price")],
        [{ left: "p", right: "pr", type: "inner", condition: "p.ppid = pr.ppid AND pr.vatCountry = 'GB'" }]
      ),
      POOL
    );
    expect(entries[1].join).toEqual(custom("p.ppid = pr.ppid AND pr.vatCountry = 'GB'", "inner"));
  });
  it("dedupes a pool that lists the same path twice instead of calling it ambiguous", () => {
    const { entries, unmatched } = resolveConvertedSources(conv([src("g", "program.program")]), [PROGRAM, { ...PROGRAM }]);
    expect(unmatched).toEqual([]);
    expect(entries).toHaveLength(1);
  });
});

describe("buildMappingConfig", () => {
  const product = entry(PRODUCT, "product");
  const mappings = [{ type: "one_to_one", source: "product.ppid", target: "ppid" }];

  it("produces no sources and no joins when nothing is selected (regression: no fallback table)", () => {
    const cfg = buildMappingConfig({ feedSources: [], mappings, filters: [] });
    expect(cfg.sources).toEqual([]);
    expect(cfg.joins).toEqual([]);
    expect(JSON.stringify(cfg)).not.toContain("gold_product");
    expect(JSON.stringify(cfg)).not.toContain("eds_dev");
  });
  it("maps one source with the placeholders the publisher rewrites", () => {
    const cfg = buildMappingConfig({ feedSources: [product], mappings, filters: ["  product.isbn13 IS NOT NULL "] });
    expect(cfg).toEqual({
      version: "1.0",
      sources: [{ name: "product", path: PRODUCT.path, format: "iceberg" }],
      joins: [],
      mappings,
      filters: ["product.isbn13 IS NOT NULL"],
      validations: [],
      rejected_path: "s3://placeholder/rejected/${batch_id}/",
      output: {
        targets: [{ format: "parquet", mode: "overwrite", path: "s3://placeholder/staging/${batch_id}/canonical/" }],
      },
    });
    expect(buildMappingConfig({ feedSources: [product], mappings }).filters).toEqual([]);
    // one compiled string per complete clause; blanks never reach the engine
    expect(buildMappingConfig({ feedSources: [product], mappings, filters: ["a = 1", "", "  "] }).filters).toEqual(["a = 1"]);
  });
  it("gives a csv source its reader options", () => {
    const csv = entry({ id: "demo.products_csv", path: "s3://bucket/raw/products/", kind: "csv" }, "products");
    expect(buildMappingConfig({ feedSources: [csv], mappings }).sources[0]).toEqual({
      name: "products",
      path: "s3://bucket/raw/products/",
      format: "csv",
      options: { header: "true", inferSchema: "true" },
    });
  });
  it("attaches each joined source where its condition says", () => {
    const cfg = buildMappingConfig({
      feedSources: [
        product,
        entry(PRICE, "price", pairs("product.ppid", "ppid")),
        entry(STOCK, "stock", pairs("price.ppid", "ppid", "inner")),
        entry(PROGRAM, "program", custom("product.ppid = program.ppid AND program.name IS NOT NULL", "full")),
      ],
      mappings,
    });
    expect(cfg.joins).toEqual([
      { left: "product", right: "price", type: "left", condition: "product.ppid = price.ppid" },
      { left: "price", right: "stock", type: "inner", condition: "price.ppid = stock.ppid" },
      { left: "product", right: "program", type: "full", condition: "product.ppid = program.ppid AND program.name IS NOT NULL" },
    ]);
    expect(cfg.sources.map((s) => s.name)).toEqual(["product", "price", "stock", "program"]);
  });
  it("emits a cross join with an empty condition (the contract the loader and validator accept)", () => {
    const cfg = buildMappingConfig({
      feedSources: [product, entry(PRICE, "price", { type: "cross", pairs: [{ leftRef: "product.ppid", rightColumn: "ppid" }], condition: null })],
      mappings,
    });
    expect(cfg.joins).toEqual([{ left: "product", right: "price", type: "cross", condition: "" }]);
  });
  it("always seeds the engine from the driving source (joins[0].left === sources[0].name)", () => {
    const shapes = [
      [product, entry(PRICE, "price", pairs("product.ppid", "ppid"))],
      [product, entry(PRICE, "price", custom("price.vatCountry = 'GB'"))],
      [product, entry(PRICE, "price", { type: "cross", pairs: [], condition: null })],
      [entry(PRICE, "price"), entry(PRODUCT, "product", pairs("price.ppid", "ppid")), entry(STOCK, "stock", pairs("product.ppid", "ppid"))],
    ];
    for (const feedSources of shapes) {
      const cfg = buildMappingConfig({ feedSources, mappings });
      expect(cfg.joins[0].left).toBe(cfg.sources[0].name);
    }
  });
});

describe("JOIN_TYPES / explainJoin / describeJoin", () => {
  it("offers the four attachment types", () => {
    expect(JOIN_TYPES).toEqual(["left", "inner", "full", "cross"]);
  });
  it("names both aliases for every engine type and never throws", () => {
    for (const type of ["left", "inner", "full", "cross", "right", "left_semi", "left_anti", "weird"]) {
      const text = explainJoin(type, "product", "price");
      expect(text).toContain("price");
      expect(typeof text).toBe("string");
    }
    expect(explainJoin("left", "product", "price")).toContain("every product row");
    expect(explainJoin("cross", "product", "price")).toContain("no condition");
    expect(explainJoin(undefined, "product", "price")).toBe(explainJoin("left", "product", "price"));
  });
  it("summarises a join with and without a condition", () => {
    expect(describeJoin(entry(PRICE, "price", pairs("product.ppid", "ppid")))).toBe(
      "price · left join on product.ppid = price.ppid"
    );
    expect(describeJoin(entry(PRICE, "price", { type: "cross", pairs: [], condition: null }))).toBe("price · cross join");
  });
});

// UIFPE-701: a ticked attribute clicked again leaves the mapping
describe("unmapColumn", () => {
  const ms = [
    { type: "one_to_one", source: "product.isbn13", target: "isbn13" },
    { type: "derived", expression: "to_json(product.prices)", target: "prices" },
    { type: "derived", expression: "upper(product.isbn13)", target: "isbn_upper" },
  ];
  it("removes the bare and the wrapped row for that column", () => {
    expect(unmapColumn(ms, "product", "isbn13").kept.map((m) => m.target)).toEqual(["prices", "isbn_upper"]);
    expect(unmapColumn(ms, "product", "prices").removed.map((m) => m.target)).toEqual(["prices"]);
  });
  it("keeps rows that only use the column inside an expression", () => {
    expect(unmapColumn(ms, "product", "title").removed).toEqual([]);
  });
});
