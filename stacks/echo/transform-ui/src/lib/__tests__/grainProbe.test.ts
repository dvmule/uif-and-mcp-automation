// The grain test run's client half (UIFPE-219): what the panel sends the
// service. The mapping is the part worth pinning — a name mapped to the
// wrong table would test the wrong data and hand back a confident, wrong
// verdict.
import { describe, it, expect } from "vitest";
import { buildAliasTables } from "../grainProbe";
import { emptyWizard } from "../types";
import type { WizardState } from "../types";

const state = (over: Partial<WizardState> = {}): WizardState => ({
  ...emptyWizard(),
  domain: "product",
  destinationTable: "gold_product.product",
  drivingTable: "MTL_SYSTEM_ITEMS_B",
  sources: [],
  ...over,
});

describe("buildAliasTables", () => {
  it("maps registered aliases to their manifest targets, {env} intact", () => {
    // the real product manifest registers items_ext over
    // uif_echo_{env}_silver_mdm.ego_mtl_sy_items_ext_b — the service, not
    // the browser, decides which environment {env} means
    const map = buildAliasTables(
      "SELECT INVENTORY_ITEM_ID FROM items_ext", state());
    expect(map.items_ext).toMatch(/^uif_echo_\{env\}_silver_mdm\./);
  });

  it("maps a task's declared source by its qualified name", () => {
    const map = buildAliasTables(
      "SELECT X FROM MTL_RELATED_ITEMS",
      state({ sources: [{ table: "uif_echo_{env}_silver_mdm.mtl_related_items",
                          eav: false, joinType: "left", joins: [] }] }));
    expect(map.MTL_RELATED_ITEMS)
      .toBe("uif_echo_{env}_silver_mdm.mtl_related_items");
  });

  it("omits what it cannot map — the advisory checks own that refusal", () => {
    const map = buildAliasTables(
      "SELECT X FROM TOTALLY_UNKNOWN", state());
    expect(map).toEqual({});
  });

  it("never maps database-qualified names — those are flagged, not tested", () => {
    const map = buildAliasTables(
      "SELECT X FROM some_db.some_table", state());
    expect(map).toEqual({});
  });
});
