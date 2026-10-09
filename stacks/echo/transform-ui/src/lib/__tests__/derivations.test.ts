// Derived columns display (ADR-0008 / UIFPE-217): the mapping carries the
// query, so there is no rendering parity to pin any more — these tests cover
// the DISPLAY helpers only: the contract in words, and the wrap preview that
// mirrors derivation_chain.py's render_wrap (verbatim CTE, LEFT join on the
// contract, _ID entity columns CAST AS BIGINT — the A3 rule).
import { describe, it, expect } from "vitest";
import { contractLines, isQueryBlock, wrapPreview } from "../derivations";
import type { DerivationBlock } from "../types";

const BLOCK: DerivationBlock = {
  sql: "SELECT e.C_EXT_ATTR1 AS k, min(e.C_EXT_ATTR2) AS v\n"
    + "FROM items_ext e WHERE e.ATTR_GROUP_ID = 999 GROUP BY 1",
  keyedBy: [{ queryColumn: "k", entityColumn: "SEGMENT1" }],
  valueColumn: "v",
  reads: { items_ext: ["ATTR_GROUP_ID", "C_EXT_ATTR1", "C_EXT_ATTR2"] },
};

describe("derived column display", () => {
  it("states the contract in words, including the computed reads", () => {
    const lines = contractLines("myCol", BLOCK);
    expect(lines[0]).toContain("k identifies it via SEGMENT1");
    expect(lines[1]).toBe("value column: v");
    expect(lines[2]).toContain("items_ext (3 columns)");
    expect(lines[2]).toContain("never typed");
  });

  it("valueColumn defaults to the column name", () => {
    const { valueColumn, ...rest } = BLOCK;
    expect(contractLines("myCol", rest)[1]).toBe("value column: myCol");
    expect(wrapPreview("myCol", rest)).toContain("d_myCol.myCol AS myCol");
  });

  it("the wrap preview embeds the query verbatim and joins LEFT on the contract", () => {
    const out = wrapPreview("myCol", BLOCK);
    expect(out).toContain("d_myCol AS (");
    for (const line of BLOCK.sql.split("\n")) {
      expect(out).toContain(`  ${line}`);
    }
    expect(out).toContain("LEFT JOIN d_myCol ON items.SEGMENT1 = d_myCol.k");
    expect(out).toContain("d_myCol.v AS myCol");
  });

  it("COALESCEs a nested column's select to array(), mirroring the compiler", () => {
    // empty arrays are [] and never NULL — the preview must show the text
    // the compiler actually writes, or the reviewer approves different SQL
    expect(wrapPreview("myArr", BLOCK, "nested"))
      .toContain("COALESCE(d_myArr.v, array()) AS myArr");
    expect(wrapPreview("myCol", BLOCK, "scalar"))
      .toContain("d_myCol.v AS myCol");
  });

  it("isQueryBlock rejects the retired step-chain shape so old drafts degrade, not crash", () => {
    expect(isQueryBlock(BLOCK)).toBe(true);
    expect(isQueryBlock({ authoritative: "sql", steps: [{ id: "s", op: "opaque" }] }))
      .toBe(false);
    expect(isQueryBlock(undefined)).toBe(false);
    expect(isQueryBlock({})).toBe(false);
  });

  it("casts _ID entity columns BIGINT in the join (the A3 rule)", () => {
    const out = wrapPreview("myCol", {
      ...BLOCK,
      keyedBy: [
        { queryColumn: "iid", entityColumn: "INVENTORY_ITEM_ID" },
        { queryColumn: "oid", entityColumn: "ORGANIZATION_ID" },
      ],
    });
    expect(out).toContain("CAST(items.INVENTORY_ITEM_ID AS BIGINT) = d_myCol.iid");
    expect(out).toContain("CAST(items.ORGANIZATION_ID AS BIGINT) = d_myCol.oid");
    expect(out).toContain("AND ");
  });
});
