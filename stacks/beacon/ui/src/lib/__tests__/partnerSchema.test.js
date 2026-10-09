// UIFPE-558: the wizard-side copy of the required-unbound rule.
// UIFPE-566: the auto-map matching rules.
import { describe, expect, it } from "vitest";
import {
  autoMapPaths,
  mappingTargets,
  missingRequired,
  pathMatchName,
  unknownTargets,
  xmlTagProblems,
} from "../partnerSchema.js";

const PATHS = [
  { path: "productId", required: true, kind: "element" },
  { path: "Identifiers/ISBN13", required: true, kind: "element" },
  { path: "Identifiers/ISBN10", required: false, kind: "element" },
  { path: "Title@lang", required: false, kind: "attribute" },
];

describe("missingRequired", () => {
  it("lists required paths the mapping does not bind", () => {
    expect(missingRequired(PATHS, ["productId"])).toEqual(["Identifiers/ISBN13"]);
  });

  it("is empty when everything required is bound", () => {
    expect(missingRequired(PATHS, ["productId", "Identifiers/ISBN13"])).toEqual([]);
  });

  it("optional paths are never demanded", () => {
    expect(missingRequired(PATHS, ["productId", "Identifiers/ISBN13", "extra"]))
      .toEqual([]);
  });

  it("tolerates missing inputs", () => {
    expect(missingRequired(undefined, undefined)).toEqual([]);
  });
});

describe("mappingTargets", () => {
  it("collects target and targets[] alike", () => {
    expect(mappingTargets([
      { target: "a" },
      { targets: ["b", "c"] },
      { expression: "no target" },
    ])).toEqual(["a", "b", "c"]);
  });
});

describe("xmlTagProblems", () => {
  it("accepts ordinary element names", () => {
    expect(xmlTagProblems({ root_tag: "productCatalog", row_tag: "product" }))
      .toEqual([]);
    expect(xmlTagProblems({ root_tag: "ONIX_Message", row_tag: "Product-1.0" }))
      .toEqual([]);
  });

  it("treats not-set as valid — that is what keeps the defaults", () => {
    expect(xmlTagProblems({})).toEqual([]);
    expect(xmlTagProblems({ root_tag: "", row_tag: "" })).toEqual([]);
    expect(xmlTagProblems(undefined)).toEqual([]);
  });

  it("refuses a name with a space, and says which field", () => {
    const [problem] = xmlTagProblems({ row_tag: "my product" });
    expect(problem).toContain("Row tag");
    expect(problem).toContain("not a valid element name");
  });

  it("refuses a colon, because namespaces are not supported", () => {
    const [problem] = xmlTagProblems({ root_tag: "onix:Message" });
    expect(problem).toContain("Root tag");
    expect(problem).toContain("namespaces are not supported");
  });

  it("refuses a name starting with a digit", () => {
    expect(xmlTagProblems({ row_tag: "1product" })).toHaveLength(1);
  });

  it("reports both fields when both are wrong", () => {
    expect(xmlTagProblems({ root_tag: "a b", row_tag: "c d" })).toHaveLength(2);
  });
});

describe("unknownTargets", () => {
  // the API's `declared` list: every path the schema mentions, any level
  const DECLARED = ["productId", "Identifiers", "Identifiers/ISBN13",
                    "Identifiers/ISBN10", "Title", "Title@lang"];

  it("names targets the schema never declares", () => {
    expect(unknownTargets(DECLARED, [
      "productId", "Identifiers", "Title@lang",
      "organization_code", "Contributors/Contributor",
    ])).toEqual(["organization_code", "Contributors/Contributor"]);
  });

  it("reports each unknown target once", () => {
    expect(unknownTargets(DECLARED, ["extra", "extra"])).toEqual(["extra"]);
  });

  it("an inner element counts as declared", () => {
    expect(unknownTargets(DECLARED, ["Identifiers"])).toEqual([]);
  });

  it("an undeclared attribute on a declared element is reported", () => {
    expect(unknownTargets(DECLARED, ["Title@dir"])).toEqual(["Title@dir"]);
  });

  it("says nothing when there is no schema", () => {
    expect(unknownTargets(undefined, ["anything"])).toEqual([]);
    expect(unknownTargets([], ["anything"])).toEqual([]);
  });

  it("tolerates missing targets", () => {
    expect(unknownTargets(DECLARED, undefined)).toEqual([]);
  });
});

describe("pathMatchName", () => {
  it("is the last element of a chain", () => {
    expect(pathMatchName("Identifiers/ISBN13")).toBe("ISBN13");
    expect(pathMatchName("productId")).toBe("productId");
  });

  it("is the attribute's own name for @ paths", () => {
    expect(pathMatchName("Title@lang")).toBe("lang");
    expect(pathMatchName("@id")).toBe("id");
  });
});

describe("autoMapPaths", () => {
  const COLS = [
    { alias: "product", column: "product_id" },
    { alias: "product", column: "isbn13" },
    { alias: "product", column: "description" },
  ];

  it("matches ignoring case, underscores and camelCase", () => {
    const { matched, unmatched } = autoMapPaths(PATHS, COLS);
    expect(matched).toEqual([
      { path: "productId", source: "product.product_id" },
      { path: "Identifiers/ISBN13", source: "product.isbn13" },
    ]);
    expect(unmatched).toEqual(["Identifiers/ISBN10", "Title@lang"]);
  });

  it("never uses one column for two paths", () => {
    const { matched, unmatched } = autoMapPaths(
      [{ path: "productId" }, { path: "Ids/PRODUCT_ID" }],
      [{ alias: "p", column: "product_id" }]
    );
    expect(matched).toEqual([{ path: "productId", source: "p.product_id" }]);
    expect(unmatched).toEqual(["Ids/PRODUCT_ID"]);
  });

  it("the driving source wins a tie between sources", () => {
    const { matched } = autoMapPaths(
      [{ path: "productId" }],
      [{ alias: "a", column: "productid" }, { alias: "b", column: "product_id" }]
    );
    expect(matched[0].source).toBe("a.productid");
  });

  it("tolerates missing inputs", () => {
    expect(autoMapPaths(undefined, undefined)).toEqual({ matched: [], unmatched: [] });
  });
});
