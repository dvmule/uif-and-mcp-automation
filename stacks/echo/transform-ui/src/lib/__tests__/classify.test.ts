// The AUTO/PLAN mirror, checked two ways.
//
// 1. Unit cases for each class and each intent override.
// 2. PARITY against the compiler itself: __fixtures__/classificationVerdicts.json
//    holds the verdict compile_intake_delta.py reaches for all 62 registered
//    columns, per intent. That file is regenerated and asserted from the Python
//    side by qe/test_ui_classification_parity.py, so if either implementation
//    changes its rules one of the two suites fails. A mirror without this is a
//    guess with a nice API.
import { describe, it, expect } from "vitest";
import type { ConditionalColumn, MappingColumn, NestedColumn, ScalarColumn } from "../types";
import { classifyColumn, verdictFor, verdictSentence, tally } from "../classify";
import realMapping from "@schemas/mapping/product/v1/product.mapping.json";
import fixture from "../__fixtures__/classificationVerdicts.json";

const DRIVING = "MTL_SYSTEM_ITEMS_B";
const chan = { event: true, message: true, api: true };

const scalar = (over: Partial<ScalarColumn> = {}): ScalarColumn => ({
  goldColumnName: "x", businessDescription: "d", kind: "scalar",
  dataType: "string",
  source: { table: DRIVING, column: "SEGMENT1", filter: null },
  transform: null, channels: chan, nullable: false, notes: null, ...over,
});

describe("classify — the column's own shape", () => {
  it("calls a driving-table scalar 'driving'", () => {
    expect(classifyColumn(scalar(), DRIVING)).toBe("driving");
  });

  it("matches the driving table on its last segment, case-insensitively", () => {
    expect(classifyColumn(
      scalar({ source: { table: "uif_echo_dev_silver_mdm.mtl_system_items_b",
                         column: "C", filter: null } }),
      "MTL_SYSTEM_ITEMS_B")).toBe("driving");
  });

  it("extracts the attribute group for an EAV source", () => {
    expect(classifyColumn(scalar({
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
                filter: "ATTR_GROUP_ID = 1168" } }), DRIVING)).toBe("eav:1168");
  });

  it("tolerates whitespace around the attribute group predicate", () => {
    expect(classifyColumn(scalar({
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C",
                filter: "ATTR_GROUP_ID=802" } }), DRIVING)).toBe("eav:802");
  });

  it("needs BOTH the EXT_B suffix and the group — the suffix alone is not enough", () => {
    // genericity limit, reproduced from the compiler on purpose: the AUTO fast
    // path is gated on an Oracle PDH naming convention, so a structurally
    // identical attribute store in another domain falls through to a join class
    expect(classifyColumn(scalar({
      source: { table: "CUSTOMER_ATTRIBUTES", column: "V",
                filter: "ATTR_GROUP_ID = 1", join: { on: [
                  { leftTable: DRIVING, leftColumn: "ID", rightColumn: "ID" }] } },
    }), DRIVING)).toBe("lookup-join");
    expect(classifyColumn(scalar({
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C", filter: null,
                join: { on: [{ leftTable: DRIVING, leftColumn: "ID", rightColumn: "ID" }] } },
    }), DRIVING)).toBe("lookup-join");
  });

  it("calls a joined non-driving source a lookup join — AUTO since UIFPE-106", () => {
    expect(classifyColumn(scalar({
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE", filter: null,
                join: { on: [{ leftTable: DRIVING, leftColumn: "ORGANIZATION_ID",
                               rightColumn: "ORGANIZATION_ID" }] } } }),
      DRIVING)).toBe("lookup-join");
  });

  it("calls an unjoined foreign source unrecognized", () => {
    expect(classifyColumn(scalar({
      source: { table: "SOMEWHERE_ELSE", column: "C", filter: null } }),
      DRIVING)).toBe("plan:unrecognized-source");
  });

  it("classes non-scalar kinds by their kind (a branchless conditional is PLAN)", () => {
    const cond = { ...scalar(), kind: "conditional" } as unknown as ConditionalColumn;
    const nest = { ...scalar(), kind: "nested" } as unknown as NestedColumn;
    expect(classifyColumn(cond, DRIVING)).toBe("plan:conditional");
    expect(classifyColumn(nest, DRIVING)).toBe("plan:nested");
  });
});

// ── conditionals & lookup-joins (UIFPE-106) ────────────────────────────────
const eavBranch = (group: number, condition: string, over: object = {}) => ({
  condition,
  source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
            filter: `ATTR_GROUP_ID = ${group}` },
  transform: null, fallback: "returnNull" as const, notes: null, ...over,
});

const conditional = (over: object = {}): ConditionalColumn => ({
  goldColumnName: "testCond", businessDescription: "d", kind: "conditional",
  dataType: "string",
  discriminator: { table: "EGO_MTL_SY_ITEMS_EXT_B",
                   column: "ITEM_CATALOG_GROUP_ID" },
  channels: chan,
  branches: [eavBranch(990, "ELSE"), eavBranch(991, "10002 (Digital)")],
  transform: null, notes: null, ...over,
} as ConditionalColumn);

describe("classify — conditionals (UIFPE-106)", () => {
  it("calls an all-EAV top-level conditional AUTO", () => {
    expect(classifyColumn(conditional(), DRIVING)).toBe("conditional");
  });

  it("keeps a struct MEMBER conditional PLAN — the named_struct is hand-written", () => {
    expect(classifyColumn(
      conditional({ goldColumnName: "fulfillmentOrgDetails.testCond" }),
      DRIVING)).toBe("plan:conditional");
  });

  it("keeps a declared MULTI-HOP derivation PLAN (the parentProduct convention)", () => {
    expect(classifyColumn(
      conditional({ notes: "MULTI-HOP: ISBN -> owning item -> PPID" }),
      DRIVING)).toBe("plan:conditional");
  });

  it("keeps a fallbackToElse branch PLAN — no deterministic strict-pick rendering", () => {
    expect(classifyColumn(conditional({
      branches: [eavBranch(990, "ELSE"),
                 eavBranch(991, "10002", { fallback: "fallbackToElse" })],
    }), DRIVING)).toBe("plan:conditional");
  });

  it("keeps a non-EAV branch PLAN — mixed-source conditionals are hand-written", () => {
    expect(classifyColumn(conditional({
      branches: [eavBranch(990, "ELSE"),
                 { ...eavBranch(0, "10002"),
                   source: { table: "MTL_PARAMETERS", column: "X", filter: null } }],
    }), DRIVING)).toBe("plan:conditional");
  });

  it("keeps a rich branch filter PLAN — the CTE writer would silently drop the extra conjunct", () => {
    expect(classifyColumn(conditional({
      branches: [eavBranch(990, "ELSE"),
                 { ...eavBranch(991, "10002"),
                   source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C",
                             filter: "ATTR_GROUP_ID = 991 AND C_EXT_ATTR9 IS NOT NULL" } }],
    }), DRIVING)).toBe("plan:conditional");
  });

  it("keeps a branch-level transform PLAN — outside the CTE it could revive NULL and break the strict pick", () => {
    expect(classifyColumn(conditional({
      branches: [eavBranch(990, "ELSE"),
                 eavBranch(991, "10002", { transform: { steps: [{ op: "trim" }] } })],
    }), DRIVING)).toBe("plan:conditional");
  });

  it("MODIFYING a conditional is PLAN, from the intent", () => {
    const v = verdictFor(conditional(), { drivingTable: DRIVING, intent: "modify" });
    expect(v).toEqual({ verdict: "PLAN", reason: "plan:conditional-modify",
                        fromIntent: true });
    expect(verdictSentence(v)).toContain("would have compiled on its own");
  });

  it("MODIFYING a lookup-join is PLAN, from the intent", () => {
    const col = scalar({
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE", filter: null,
                join: { on: [{ leftTable: DRIVING, leftColumn: "ORGANIZATION_ID",
                               rightColumn: "ORGANIZATION_ID" }] } } });
    expect(verdictFor(col, { drivingTable: DRIVING, intent: "modify" }))
      .toEqual({ verdict: "PLAN", reason: "plan:lookup-join-modify",
                 fromIntent: true });
    expect(verdictFor(col, { drivingTable: DRIVING, intent: "add" }).verdict)
      .toBe("AUTO");
  });

  it("keeps a CHAINED lookup PLAN — an on-pair anchored off the driving table has no dimension-CTE rendering", () => {
    expect(classifyColumn(scalar({
      source: { table: "MTL_SYSTEM_ITEMS_TL", column: "DESCRIPTION", filter: null,
                join: { on: [{ leftTable: "MTL_PARAMETERS",
                               leftColumn: "ORGANIZATION_ID",
                               rightColumn: "ORGANIZATION_ID" }] } } }),
      DRIVING)).toBe("plan:lookup-join");
  });

  it("a derivation block is derived-sql on any kind — AUTO on add, PLAN on modify (ADR-0008)", () => {
    const block = {
      sql: "SELECT e.A AS k, min(e.B) AS v FROM items_ext e GROUP BY 1",
      keyedBy: [{ queryColumn: "k", entityColumn: "SEGMENT1" }],
      valueColumn: "v",
    };
    expect(classifyColumn(scalar({ derivation: block }), DRIVING)).toBe("derived-sql");
    expect(classifyColumn(conditional({ derivation: block }), DRIVING)).toBe("derived-sql");
    const add = verdictFor(scalar({ derivation: block }),
      { drivingTable: DRIVING, intent: "add" });
    expect(add.verdict).toBe("AUTO");
    expect(verdictSentence(add)).toContain("exactly as written");
    const mod = verdictFor(scalar({ derivation: block }),
      { drivingTable: DRIVING, intent: "modify" });
    expect(mod.verdict).toBe("PLAN");
    expect(mod.reason).toBe("plan:derived-sql-modify");
    expect(mod.fromIntent).toBe(true);
    // an EMPTY block still classifies derived-sql — mirroring the Python
    // `is not None`, so both sides route it to the battery's refusal
    // instead of a kind-shaped writer that would drop it
    expect(classifyColumn(scalar({ derivation: {} as never }), DRIVING))
      .toBe("derived-sql");
  });

  it("keeps a non-LEFT lookup PLAN — INNER drops driving rows, which the CTE shape cannot render", () => {
    expect(classifyColumn(scalar({
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE", filter: null,
                join: { type: "inner",
                        on: [{ leftTable: DRIVING, leftColumn: "ORGANIZATION_ID",
                               rightColumn: "ORGANIZATION_ID" }] } } }),
      DRIVING)).toBe("plan:lookup-join");
  });
});

describe("intent overrides — where a naive mirror lies", () => {
  const drivingCol = scalar({ goldColumnName: "ppid" });
  const eavCol = scalar({ goldColumnName: "listPrice",
    source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
              filter: "ATTR_GROUP_ID = 1168" } });

  it("adds a driving column as AUTO", () => {
    expect(verdictFor(drivingCol, { drivingTable: DRIVING, intent: "add" }))
      .toEqual({ verdict: "AUTO", reason: "driving", fromIntent: false });
  });

  it("MODIFYING a driving column is PLAN, and says the intent caused it", () => {
    const v = verdictFor(drivingCol, { drivingTable: DRIVING, intent: "modify" });
    expect(v.verdict).toBe("PLAN");
    expect(v.reason).toBe("plan:driving-modify");
    expect(v.fromIntent).toBe(true);
    expect(verdictSentence(v)).toContain("adding a new one");
  });

  it("keeps an EAV column AUTO on modify — the compiler has a modify path for it", () => {
    expect(verdictFor(eavCol, { drivingTable: DRIVING, intent: "modify" }).verdict)
      .toBe("AUTO");
  });

  it("treats a kind change as PLAN, naming both kinds", () => {
    const nowConditional = { ...eavCol, kind: "conditional" } as unknown as MappingColumn;
    const v = verdictFor(nowConditional, {
      drivingTable: DRIVING, intent: "modify",
      registeredKinds: { listPrice: "scalar" } });
    expect(v.reason).toBe("plan:kind-change-scalar-to-conditional");
    expect(v.fromIntent).toBe(true);
    expect(verdictSentence(v)).toContain("scalar to conditional");
  });

  it("lets the kind change win over an otherwise-AUTO class", () => {
    // the compiler applies the kind-change override BEFORE the eav dispatch
    const stillEav = { ...eavCol, kind: "nested" } as unknown as MappingColumn;
    expect(verdictFor(stillEav, { drivingTable: DRIVING, intent: "modify",
      registeredKinds: { listPrice: "scalar" } }).reason)
      .toBe("plan:kind-change-scalar-to-nested");
  });

  it("ignores registered kinds on an add — a new column has no prior shape", () => {
    expect(verdictFor(drivingCol, { drivingTable: DRIVING, intent: "add",
      registeredKinds: { ppid: "nested" } }).verdict).toBe("AUTO");
  });
});

describe("tally", () => {
  it("counts reasons and how many are only PLAN because of the intent", () => {
    const t = tally([
      { verdict: "AUTO", reason: "driving", fromIntent: false },
      { verdict: "PLAN", reason: "plan:conditional", fromIntent: false },
      { verdict: "PLAN", reason: "plan:conditional", fromIntent: false },
      { verdict: "PLAN", reason: "plan:driving-modify", fromIntent: true },
    ]);
    expect(t).toEqual({ auto: 1, plan: 3, intentDriven: 1,
      byReason: { "plan:conditional": 2, "plan:driving-modify": 1 } });
  });
});

// ── parity with the compiler ────────────────────────────────────────────────
describe("parity with compile_intake_delta.py", () => {
  const m = realMapping as any;
  const cols = m.columns as MappingColumn[];
  const driving: string = m.drivingTable;
  const registeredKinds: Record<string, MappingColumn["kind"]> =
    Object.fromEntries(cols.map((c) => [c.goldColumnName, c.kind]));

  const flat = (intent: "add" | "modify") =>
    Object.fromEntries(cols.map((c) => {
      const v = verdictFor(c, { drivingTable: driving, intent, registeredKinds });
      return [c.goldColumnName, v.verdict === "AUTO" ? "AUTO" : v.reason];
    }));

  it("covers every registered column in the fixture", () => {
    expect(Object.keys(fixture.add)).toHaveLength(cols.length);
    expect(Object.keys(fixture.modify)).toHaveLength(cols.length);
  });

  it("reaches the compiler's verdict for every column, on ADD", () => {
    expect(flat("add")).toEqual(fixture.add);
  });

  it("reaches the compiler's verdict for every column, on MODIFY", () => {
    expect(flat("modify")).toEqual(fixture.modify);
  });

  it("shows the intent swing the wizard's most-travelled path depends on", () => {
    const add = tally(cols.map((c) =>
      verdictFor(c, { drivingTable: driving, intent: "add", registeredKinds })));
    const mod = tally(cols.map((c) =>
      verdictFor(c, { drivingTable: driving, intent: "modify", registeredKinds })));
    // 50 -> 13: every driving-table scalar, conditional, lookup-join and
    // derived column flips on modify. Pinned so a change to either side of
    // the mirror has to be deliberate. (UIFPE-106: add-path AUTO 25 -> 48 —
    // 21 top-level EAV conditionals + 2 lookup-joins; UIFPE-217/ADR-0008:
    // parentProduct's query splices on add -> 49; UIFPE-232: categories
    // carries its own query too -> 50. All intent-driven PLANs on modify,
    // joining the 12 driving-table scalars: 37 total.)
    // 54 since 2026-09-07 (UIFPE-269/270): +2 course-duration conditionals,
    // +2 BOM arrays now derived-sql (their blocks landed)
    // 56 since 2026-09-09 (UIFPE-354): + fulfillmentOrgDetails.boFlag (a
    // LOOKUP JOIN — its compound filter means the group is not the whole
    // predicate) and .defaultSoSourceType (driving-table scalar)
    // ALL SEVEN re-derived 2026-09-30 from the fixture, which is 74 columns
    // now. Every number below had drifted, not just the first -- this test
    // stops at its first failing assertion, so one stale pin hid the other
    // six, and no CI ran the suite to surface any of them (UIFPE-738). The
    // numbers come from the PYTHON-OWNED fixture, not from what this
    // classifier happens to return, so they remain an independent claim.
    expect(add.auto).toBe(60);
    expect(mod.auto).toBe(14);
    // decomposition is the four byReason pins below: 14 + 24 + 3 + 5
    expect(mod.intentDriven).toBe(46);
    expect(mod.byReason["plan:driving-modify"]).toBe(14);
    expect(mod.byReason["plan:conditional-modify"]).toBe(24);
    // 3 since UIFPE-354: boFlag joins EGO_MTL_SY_ITEMS_EXT_B with a COMPOUND
    // EAV predicate, so classify reads it as a lookup join rather than an
    // eav:<group> column — the group alone is not the whole filter.
    expect(mod.byReason["plan:lookup-join-modify"]).toBe(3);
    // parentProduct + categories + standardComponents + customComponents
    expect(mod.byReason["plan:derived-sql-modify"]).toBe(5);
  });
});
