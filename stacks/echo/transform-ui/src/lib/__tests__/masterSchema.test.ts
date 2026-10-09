// master.json as the primary artifact, checked against the real registered
// schema. The load-bearing assertion is the PK one: master.json's `required` is
// NOT a key declaration, and a UI that treated it as one would show the wrong
// grain for the product entity (3 names vs the real 2).
import { describe, it, expect } from "vitest";
import {
  MASTERS, MASTER_DOMAINS, findMaster, mastersFor, primaryKeyOf,
  unmappedAttributes, unregisteredMappings, artifactPaths,
} from "../masterSchema";
import rawProduct from "@schemas/gold/product/v1/master.json";

describe("discovery", () => {
  it("finds the registered gold schema across domains", () => {
    expect(MASTERS.length).toBeGreaterThan(0);
    const p = findMaster("product", "product");
    expect(p).toBeDefined();
    expect(p!.version).toBe("v1");
    expect(p!.path).toBe(
      "domains/product/src/schemas/gold/product/v1/master.json");
  });

  it("reads only the domains that actually register a schema", () => {
    // Four of the thirteen settled domains register a gold schema; the rest
    // hold a stamped README and nothing else. `program` joined when
    // UIFPE-119/120 landed its master.json; `price` and `stock` joined
    // 2026-08-17 when their schemas relocated out of domains/product/ into
    // their own domains. This assertion is a deliberate canary — the next
    // domain registering a schema is meant to break it and be acknowledged
    // here.
    expect(MASTER_DOMAINS).toEqual(["price", "product", "program", "stock"]);
    // The real invariant, independent of how many domains exist: a domain with
    // no registered schema contributes nothing rather than an empty stub.
    expect(mastersFor("customer")).toEqual([]);
    expect(mastersFor("legal")).toEqual([]);
  });

  it("carries the schema's own metadata rather than inventing any", () => {
    const p = findMaster("product", "product")!;
    expect(p.metadata).toMatchObject({ domain: "product", lifecycle: "active" });
  });
});

describe("attributes", () => {
  const p = () => findMaster("product", "product")!;

  it("lists every property with its business meaning", () => {
    // Compared against the master's OWN property list, not a typed count. The
    // master grows (UIFPE-232, UIFPE-270, ...) and a pinned number records only
    // when someone last looked -- it read 60 while the file held 63, and no CI
    // ran this suite to say so (UIFPE-738). Names, not a length, so a dropped
    // or invented attribute fails too.
    expect(p().attributes.map((a) => a.name).sort())
      .toEqual(Object.keys(rawProduct.properties).sort());
    // "with its business meaning" is the claim in the name of this test
    expect(p().attributes.filter((a) => !a.description?.trim())).toEqual([]);
    const productId = p().attributes.find((a) => a.name === "productId")!;
    expect(productId.description).toContain("Pearson Product Identifier");
  });

  it("strips the null union and reports nullability separately", () => {
    const isbn = p().attributes.find((a) => a.name === "isbn13")!;
    expect(isbn.type).toBe("string");
    expect(isbn.nullable).toBe(true);
    const id = p().attributes.find((a) => a.name === "inventoryItemId")!;
    expect(id.type).toBe("integer");
    expect(id.nullable).toBe(false);
  });

  it("reads consumer tiers, including internal-only attributes", () => {
    const id = p().attributes.find((a) => a.name === "inventoryItemId")!;
    expect(id.channels).toEqual([]);          // internal only
    const productId = p().attributes.find((a) => a.name === "productId")!;
    expect(productId.channels).toEqual(["event", "message", "api"]);
    // every property declares its tier — the tier subsets depend on it
    expect(p().attributes.every((a) => Array.isArray(a.channels))).toBe(true);
  });

  it("descends into array and object shapes", () => {
    const arrays = p().attributes.filter((a) => a.type === "array");
    expect(arrays.length).toBeGreaterThan(0);
    const withChildren = arrays.find((a) => a.children.length > 0);
    expect(withChildren, "at least one array declares its element shape")
      .toBeDefined();
    expect(withChildren!.children[0].name).toBeTruthy();
  });
});

describe("primary key", () => {
  it("comes from master.json now that it declares one (UIFPE-232), and "
     + "says so", () => {
    // product's master gained an explicit primaryKey with the categories
    // change — the schema is now the authority, the mapping the fallback
    const r = primaryKeyOf("product", "product");
    expect(r.source).toBe("master");
    expect(r.key.map((p) => p.goldColumn)).toEqual(
      ["inventoryItemId", "organizationId"]);
  });

  it("flags that `required` disagrees with the key", () => {
    // master.required = [inventoryItemId, productId, organizationId] — productId is
    // required but is not part of the grain. Reading `required` as a key would
    // put a third column in the PK.
    const r = primaryKeyOf("product", "product");
    expect(r.requiredDiffers).toBe(true);
    expect(findMaster("product", "product")!.requiredNames).toContain("productId");
    expect(r.key.map((p) => p.goldColumn)).not.toContain("productId");
  });

  it("reports no key for an entity with neither schema nor mapping", () => {
    expect(primaryKeyOf("customer", "customerAccount"))
      .toEqual({ key: [], source: "none", requiredDiffers: false });
  });
});

describe("drift between the primary schema and the mapping rules", () => {
  it("finds attributes with no mapping rule yet", () => {
    // both directions are computed the same way; on a reconciled entity either
    // list may be empty, so assert the shape rather than a count
    expect(Array.isArray(unmappedAttributes("product", "product"))).toBe(true);
  });

  it("finds mapping rules with no registered attribute", () => {
    const extra = unregisteredMappings("product", "product");
    expect(Array.isArray(extra)).toBe(true);
    // dotted intake names are struct MEMBERS, so they must resolve to their
    // parent attribute and not show up as unregistered
    expect(extra).not.toContain("fulfillmentOrgDetails");
  });

  it("returns nothing for an unregistered entity instead of throwing", () => {
    expect(unmappedAttributes("hr", "employee")).toEqual([]);
    expect(unregisteredMappings("hr", "employee")).toEqual([]);
  });
});

describe("artifact paths", () => {
  it("puts every domain's files under its own folder", () => {
    expect(artifactPaths("customer", "customerAccount")).toEqual({
      master: "domains/customer/src/schemas/gold/customerAccount/v1/master.json",
      mapping: "domains/customer/src/schemas/mapping/customerAccount/v1/customer.mapping.json",
      manifest: "domains/customer/src/schemas/mapping/customerAccount/v1/manifest.yaml",
      sql: "domains/customer/src/schemas/mapping/customerAccount/v1/sql/customerAccount.sql",
    });
  });

  it("matches where the product entity's files actually live", () => {
    expect(artifactPaths("product", "product").master)
      .toBe(findMaster("product", "product")!.path);
  });
});
