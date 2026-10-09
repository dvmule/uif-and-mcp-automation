// Source-attachment derivation, in the same style as wizardLogic.test.ts: the
// full matrix of attach shapes (driving / whole key / partial key / undeclared)
// × column kinds, plus a pass over the REAL registered product mapping so the
// derivation is checked against production data rather than only fixtures.
//
// The load-bearing case is a table read at MORE THAN ONE grain in the same
// mapping. The product entity does exactly that, and it is why the unit here is
// the attachment (table × join shape) and not the table.
import { describe, it, expect } from "vitest";
import type { ConditionalColumn, EntityKeyPair, NestedColumn, ScalarColumn } from "../types";
import {
  deriveSourceAttachments, attachmentsByTable, fanOutSentence, disambiguation,
  keyMappingForTable,
} from "../sourceGrain";
import realMapping from "@schemas/mapping/product/v1/product.mapping.json";

const DRIVING = "MTL_SYSTEM_ITEMS_B";
const KEY: EntityKeyPair[] = [
  { goldColumn: "inventoryItemId", drivingColumn: "INVENTORY_ITEM_ID" },
  { goldColumn: "organizationId", drivingColumn: "ORGANIZATION_ID" },
];

const chan = { event: true, message: true, api: true };

const scalar = (over: Partial<ScalarColumn> = {}): ScalarColumn => ({
  goldColumnName: "ppid", businessDescription: "d", kind: "scalar",
  dataType: "string",
  source: { table: DRIVING, column: "SEGMENT1", filter: null },
  transform: null, channels: chan, nullable: false, notes: null, ...over,
});

/** A join covering the named driving-table columns. */
const joinOn = (...cols: string[]) => ({
  type: "left" as const,
  on: cols.map((c) => ({ leftTable: DRIVING, leftColumn: c, rightColumn: c })),
});

const derive = (cols: any[], key = KEY, driving: string | null = DRIVING) =>
  deriveSourceAttachments({ drivingTable: driving, entityKey: key, columns: cols });
const find = (rows: any[], name: string, covers?: string) =>
  rows.find((r) => r.shortName === name
    && (covers === undefined || r.covers.join(",") === covers))!;

describe("attach shape", () => {
  it("marks the driving table as the grain, with nothing to repeat across", () => {
    const [a] = derive([scalar()]);
    expect(a.shape).toBe("driving");
    expect(a.repeatsAcross).toEqual([]);
  });

  it("covers the whole key when the join names every key column", () => {
    const tl = find(derive([scalar(), scalar({
      goldColumnName: "productTitle",
      source: { table: "MTL_SYSTEM_ITEMS_TL", column: "DESCRIPTION",
                filter: "LANGUAGE = 'US'",
                join: joinOn("INVENTORY_ITEM_ID", "ORGANIZATION_ID") },
    })]), "MTL_SYSTEM_ITEMS_TL");
    expect(tl.shape).toBe("full-key");
    expect(tl.covers).toEqual(["inventoryItemId", "organizationId"]);
    expect(tl.repeatsAcross).toEqual([]);
  });

  it("reports the uncovered key columns for a partial-key attach", () => {
    const ext = find(derive([scalar(), scalar({
      goldColumnName: "listPrice",
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
                filter: "ATTR_GROUP_ID = 1168", join: joinOn("INVENTORY_ITEM_ID") },
    })]), "EGO_MTL_SY_ITEMS_EXT_B");
    expect(ext.shape).toBe("partial-key");
    expect(ext.covers).toEqual(["inventoryItemId"]);
    expect(ext.repeatsAcross).toEqual(["organizationId"]);
    expect(fanOutSentence(ext)).toContain("repeats across organizationId");
  });

  it("covers the org half only for a dimension keyed on the other column", () => {
    const org = find(derive([scalar(), scalar({
      goldColumnName: "organizationCode",
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE",
                filter: null, join: joinOn("ORGANIZATION_ID") },
    })]), "MTL_PARAMETERS");
    expect(org.covers).toEqual(["organizationId"]);
    expect(org.repeatsAcross).toEqual(["inventoryItemId"]);
  });

  it("flags a non-driving source that states no join at all", () => {
    const other = find(derive([scalar(), scalar({
      goldColumnName: "orphan",
      source: { table: "SOME_OTHER_TABLE", column: "X", filter: null },
    })]), "SOME_OTHER_TABLE");
    expect(other.shape).toBe("undeclared");
  });

  it("matches bare PDH names against qualified Silver driving names", () => {
    expect(derive([scalar()], KEY,
      "uif_echo_dev_silver_mdm.mtl_system_items_b")[0].shape).toBe("driving");
  });

  it("notes a join chain through an intermediate table as indirect", () => {
    expect(find(derive([scalar(), scalar({
      goldColumnName: "viaChain",
      source: { table: "TABLE_C", column: "X", filter: null,
                join: { type: "left", on: [
                  { leftTable: "TABLE_B", leftColumn: "B_ID", rightColumn: "B_ID" }] } },
    })]), "TABLE_C").indirect).toBe(true);
  });
});

// ── the case that drove the design ──────────────────────────────────────────
describe("one table read at several grains", () => {
  const cols = [
    scalar(),
    scalar({ goldColumnName: "itemLevel",
      source: { table: "EXT", column: "C1", filter: "ATTR_GROUP_ID = 1168",
                join: joinOn("INVENTORY_ITEM_ID") } }),
    scalar({ goldColumnName: "orgLevel",
      source: { table: "EXT", column: "C2", filter: "ATTR_GROUP_ID = 822",
                join: joinOn("INVENTORY_ITEM_ID", "ORGANIZATION_ID") } }),
  ];

  it("keeps the two grains as separate attachments", () => {
    const rows = derive(cols).filter((r) => r.shortName === "EXT");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.shape).sort()).toEqual(["full-key", "partial-key"]);
  });

  it("groups them under one table for display", () => {
    const t = attachmentsByTable(derive(cols)).find((x) => x.shortName === "EXT")!;
    expect(t.attachments).toHaveLength(2);
  });

  it("takes the INTERSECTION for key_mapping — the widest fan-out is the safe one", () => {
    const t = attachmentsByTable(derive(cols)).find((x) => x.shortName === "EXT")!;
    // NOT organizationId: rows reached through the item-only join can affect
    // every org, so claiming org coverage would miss invalidations
    expect(keyMappingForTable(t.attachments, KEY))
      .toEqual({ inventoryItemId: "INVENTORY_ITEM_ID" });
  });

  it("ignores unstated attachments instead of collapsing the mapping to empty", () => {
    const withArray = [...cols, {
      goldColumnName: "arr", businessDescription: "d", kind: "nested",
      joinKey: "INVENTORY_ITEM_ID", filter: null, channels: chan, notes: null,
      fields: [{ name: "v", type: "string", sortKey: true,
                 source: { table: "EXT", column: "C9" } }],
    } as NestedColumn];
    const t = attachmentsByTable(derive(withArray)).find((x) => x.shortName === "EXT")!;
    expect(t.attachments.some((a) => a.shape === "undeclared")).toBe(true);
    expect(keyMappingForTable(t.attachments, KEY))
      .toEqual({ inventoryItemId: "INVENTORY_ITEM_ID" });
  });
});

describe("column kinds all contribute their sources", () => {
  it("reads every branch of a conditional", () => {
    const cond: ConditionalColumn = {
      goldColumnName: "coverImageLarge", businessDescription: "d",
      kind: "conditional", dataType: "string",
      discriminator: { table: "EXT", column: "ITEM_CATALOG_GROUP_ID" },
      channels: chan, notes: null,
      branches: [
        { condition: "10002", source: { table: "EXT", column: "C_EXT_ATTR3",
            filter: "ATTR_GROUP_ID = 1405", join: joinOn("INVENTORY_ITEM_ID") },
          fallback: "returnNull", notes: null },
        { condition: "ELSE", source: { table: "EXT", column: "C_EXT_ATTR3",
            filter: "ATTR_GROUP_ID = 1168", join: joinOn("INVENTORY_ITEM_ID") },
          fallback: "returnNull", notes: null },
      ],
    };
    const ext = find(derive([scalar(), cond]), "EXT");
    expect(ext.usedBy).toBe(2);
    expect(ext.filters).toEqual(["ATTR_GROUP_ID = 1405", "ATTR_GROUP_ID = 1168"]);
    expect(disambiguation(ext)).toBe("every-use-filtered");
    expect(ext.examples[0]).toBe("coverImageLarge (branch 1)");
  });

  it("reads array fields, falling back to the array's own filter", () => {
    const nested: NestedColumn = {
      goldColumnName: "contributors", businessDescription: "d", kind: "nested",
      joinKey: "INVENTORY_ITEM_ID", filter: "ATTR_GROUP_ID = 802",
      channels: chan, notes: null,
      fields: [{ name: "contributorId", type: "string", sortKey: true,
                 source: { table: "EXT", column: "C_EXT_ATTR1" } }],
    };
    const ext = find(derive([scalar(), nested]), "EXT");
    expect(ext.filters).toEqual(["ATTR_GROUP_ID = 802"]);
    expect(ext.shape).toBe("undeclared");
    expect(ext.examples).toEqual(["contributors.contributorId"]);
  });
});

describe("disambiguation", () => {
  it("separates an unfiltered use from a fully filtered attachment", () => {
    const ext = find(derive([scalar(),
      scalar({ goldColumnName: "a", source: { table: "EXT", column: "C1",
        filter: "ATTR_GROUP_ID = 1", join: joinOn("INVENTORY_ITEM_ID") } }),
      scalar({ goldColumnName: "b", source: { table: "EXT", column: "C2",
        filter: null, join: joinOn("INVENTORY_ITEM_ID") } })]), "EXT");
    expect(ext.usesWithoutFilter).toBe(1);
    expect(disambiguation(ext)).toBe("some-uses-unfiltered");
  });

  it("needs nothing for the driving table", () => {
    expect(disambiguation(derive([scalar()])[0])).toBe("not-needed");
  });
});

// ── the real mapping ────────────────────────────────────────────────────────
// Guards the derivation against the production intake, not just fixtures. The
// key_mapping assertions reproduce what the hand-written manifest declares —
// which is the whole argument for generating it.
describe("registered product mapping", () => {
  const m = realMapping as any;
  const rows = deriveSourceAttachments(m);
  const tables = attachmentsByTable(rows);
  const tbl = (n: string) => tables.find((t) => t.shortName.toUpperCase() === n)!;

  it("finds the driving table and puts it first", () => {
    expect(rows[0].shape).toBe("driving");
    expect(rows[0].shortName.toUpperCase()).toBe("MTL_SYSTEM_ITEMS_B");
  });

  it("sees the extension table at three distinct grains", () => {
    const ext = tbl("EGO_MTL_SY_ITEMS_EXT_B");
    const shapes = ext.attachments.map((a) => a.shape).sort();
    expect(shapes).toEqual(["full-key", "partial-key", "undeclared"]);
  });

  it("derives the manifest's own key_mapping for the extension table", () => {
    // manifest.yaml declares inventoryItemId only, commented "item-level
    // source: binds a PK SUBSET -> fans out to all orgs (D1)"
    expect(keyMappingForTable(tbl("EGO_MTL_SY_ITEMS_EXT_B").attachments, m.entityKey))
      .toEqual({ inventoryItemId: "INVENTORY_ITEM_ID" });
  });

  it("derives the manifest's own key_mapping for the org dimension", () => {
    // manifest.yaml declares organizationId only — one changed org row fans out
    // to every Gold row in that org
    expect(keyMappingForTable(tbl("MTL_PARAMETERS").attachments, m.entityKey))
      .toEqual({ organizationId: "ORGANIZATION_ID" });
  });

  it("derives the whole key for the translations table and the driving table", () => {
    expect(keyMappingForTable(tbl("MTL_SYSTEM_ITEMS_TL").attachments, m.entityKey))
      .toEqual({ inventoryItemId: "INVENTORY_ITEM_ID", organizationId: "ORGANIZATION_ID" });
    expect(keyMappingForTable(tbl("MTL_SYSTEM_ITEMS_B").attachments, m.entityKey))
      .toEqual({ inventoryItemId: "INVENTORY_ITEM_ID", organizationId: "ORGANIZATION_ID" });
  });

  it("never claims coverage of a key column outside the declared key", () => {
    const gold = new Set(m.entityKey.map((p: any) => p.goldColumn));
    for (const r of rows) for (const c of r.covers) expect(gold.has(c)).toBe(true);
  });
});
