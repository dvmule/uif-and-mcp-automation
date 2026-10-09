// The full scenario matrix for the wizard's pure logic — every flow
// (add / modify / new table) × every column kind (scalar / conditional /
// nested) × every source shape (driving / declared graph / EAV / structured
// lookup / missing attachment), plus naming-style identity and condition
// round-trips. This suite is the contract: the SQL preview and the validators
// must agree with each other and with the intake schema.
import { describe, it, expect } from "vitest";
import {
  ScalarColumn, ConditionalColumn, NestedColumn, SourceDecl,
  columnProblems, sourceProblems, taskProblems, normalizeColumns,
  sameTable, resolveScope, resolveTransform, entityKeyJoin, entityKeyProblems,
  convertColumnKind, rememberedJoin } from "../types";
import { previewColumnSql, transformSql } from "../sqlPreview";
import { parseCondition, formatCondition, branchWhere, scopeWhere, scopeLabel } from "../conditions";

const DRIVING = "MTL_SYSTEM_ITEMS_B";
const QUALIFIED_DRIVING = "uif_echo_dev_silver_mdm.mtl_system_items_b";

const scalar = (over: Partial<ScalarColumn> = {}): ScalarColumn => ({
  goldColumnName: "ppid", businessDescription: "Pearson Product Identifier",
  kind: "scalar", dataType: "string",
  source: { table: DRIVING, column: "SEGMENT1", filter: "SEGMENT1 LIKE 'A%'" },
  transform: null, channels: { event: true, message: true, api: true },
  nullable: false, notes: null, ...over,
});

const decl = (table: string, over: Partial<SourceDecl> = {}): SourceDecl =>
  ({ table, eav: false, joinType: "left", joins: [], ...over });

// ── table identity across naming styles ─────────────────────────────────────

describe("sameTable", () => {
  it("matches bare PDH names against qualified Silver names, any case", () => {
    expect(sameTable(DRIVING, QUALIFIED_DRIVING)).toBe(true);
    expect(sameTable("MTL_PARAMETERS", DRIVING)).toBe(false);
    expect(sameTable("", DRIVING)).toBe(false);
    expect(sameTable(null, undefined)).toBe(false);
  });

  it("treats the same table name in two DIFFERENT databases as two tables", () => {
    expect(sameTable("uif_echo_dev_silver_mdm.mtl_system_items_b",
                     "echo_dev_silver_customer.mtl_system_items_b")).toBe(false);
    expect(sameTable("uif_echo_dev_silver_mdm.mtl_system_items_b",
                     "uif_echo_dev_silver_mdm.mtl_system_items_b")).toBe(true);
  });

  it("lets {env} tokens match any environment segment", () => {
    expect(sameTable("uif_echo_{env}_silver_mdm.mtl_system_items_b",
                     QUALIFIED_DRIVING)).toBe(true);
    expect(sameTable("uif_echo_{env}_silver_mdm.mtl_system_items_b",
                     "echo_dev_silver_customer.mtl_system_items_b")).toBe(false);
  });
});

// ── scalar: driving / lookup / EAV / declared graph ─────────────────────────

describe("scalar preview", () => {
  it("driving-table source reads the table directly (no join, no placeholder)", () => {
    const sql = previewColumnSql(scalar(), [], DRIVING);
    expect(sql).toContain("FROM MTL_SYSTEM_ITEMS_B");
    expect(sql).not.toContain("JOIN");
    expect(sql).not.toContain("{driving_table}");
  });

  it("recognizes the driving table under a qualified name too", () => {
    const col = scalar({ source: { table: QUALIFIED_DRIVING, column: "SEGMENT1", filter: null } });
    expect(previewColumnSql(col, [], DRIVING)).not.toContain("JOIN");
  });

  it("structured lookup renders the dimension CTE the compiler ships (UIFPE-106)", () => {
    const col = scalar({
      goldColumnName: "organizationCode",
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE", filter: null,
        join: { type: "left", on: [{ leftTable: DRIVING,
          leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" }] } },
    });
    const sql = previewColumnSql(col, [], DRIVING);
    // the orgs-CTE shape: max() per key, _ID keys CAST to BIGINT on both sides
    expect(sql).toContain("lk_organizationCode AS (");
    expect(sql).toContain("SELECT CAST(ORGANIZATION_ID AS BIGINT) AS oid,");
    expect(sql).toContain("max(ORGANIZATION_CODE) AS organizationCode");
    expect(sql).toContain("FROM MTL_PARAMETERS");
    expect(sql).toContain("GROUP BY 1");
    expect(sql).toContain("FROM MTL_SYSTEM_ITEMS_B mtl_system_items_b");
    expect(sql).toContain(
      "LEFT JOIN lk_organizationCode ON CAST(mtl_system_items_b.ORGANIZATION_ID AS BIGINT) = lk_organizationCode.oid");
    expect(sql).toContain("lk_organizationCode.organizationCode AS organizationCode");
  });

  it("lookup filter folds INSIDE the CTE — never an outer WHERE (the longDescription divergence)", () => {
    const col = scalar({
      goldColumnName: "longDescription",
      source: { table: "MTL_SYSTEM_ITEMS_TL", column: "LONG_DESCRIPTION",
        filter: "LANGUAGE = 'US'",
        join: { type: "left", on: [
          { leftTable: DRIVING, leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" },
          { leftTable: DRIVING, leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" }] } },
    });
    const sql = previewColumnSql(col, [], DRIVING);
    // two-key dimension: iid + oid, deduped per (item, org)
    expect(sql).toContain(
      "SELECT CAST(INVENTORY_ITEM_ID AS BIGINT) AS iid, CAST(ORGANIZATION_ID AS BIGINT) AS oid,");
    expect(sql).toContain("GROUP BY 1, 2");
    expect(sql).toContain("CAST(mtl_system_items_b.INVENTORY_ITEM_ID AS BIGINT) = lk_longDescription.iid");
    expect(sql).toContain("CAST(mtl_system_items_b.ORGANIZATION_ID AS BIGINT) = lk_longDescription.oid");
    // the filter sits between the CTE's FROM and its GROUP BY…
    const cteFrom = sql.indexOf("FROM MTL_SYSTEM_ITEMS_TL");
    const where = sql.indexOf("WHERE LANGUAGE = 'US'");
    expect(where).toBeGreaterThan(cteFrom);
    expect(where).toBeLessThan(sql.indexOf("GROUP BY 1, 2"));
    // …and nothing after the join carries a WHERE that would drop unmatched rows
    expect(sql.slice(sql.indexOf("-- attaches"))).not.toContain("WHERE");
  });

  it("non-_ID lookup keys stay unCAST and alias as k1", () => {
    const col = scalar({
      goldColumnName: "orgName",
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_NAME", filter: null,
        join: { type: "left", on: [{ leftTable: DRIVING,
          leftColumn: "ORGANIZATION_CODE", rightColumn: "ORGANIZATION_CODE" }] } },
    });
    const sql = previewColumnSql(col, [], DRIVING);
    expect(sql).toContain("SELECT ORGANIZATION_CODE AS k1,");
    expect(sql).toContain("ON mtl_system_items_b.ORGANIZATION_CODE = lk_orgName.k1");
    expect(sql).not.toContain("CAST(ORGANIZATION_CODE");
  });

  it("lookup transform wraps the PIVOTED value in the final SELECT", () => {
    const col = scalar({
      goldColumnName: "organizationCode",
      source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE", filter: null,
        join: { type: "left", on: [{ leftTable: DRIVING,
          leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" }] } },
      transform: { steps: [{ op: "upper" }] },
    });
    const sql = previewColumnSql(col, [], DRIVING);
    // the CTE's max() pick stays untouched; the transform applies after
    expect(sql).toContain("max(ORGANIZATION_CODE) AS organizationCode");
    expect(sql).toContain("upper(lk_organizationCode.organizationCode) AS organizationCode");
  });

  it("non-driving source without attachment renders a VISIBLE placeholder join", () => {
    const col = scalar({ source: { table: "MTL_SYSTEM_ITEMS_TL",
      column: "LONG_DESCRIPTION", filter: "LANGUAGE='US'" } });
    const sql = previewColumnSql(col, [], DRIVING);
    expect(sql).toContain("FROM MTL_SYSTEM_ITEMS_B");
    expect(sql).toContain("LEFT JOIN MTL_SYSTEM_ITEMS_TL");
    expect(sql).toContain("attachment not defined");
  });

  it("declared join-graph source joins via the graph, FROM stays driving", () => {
    const sources = [decl(QUALIFIED_DRIVING),
      decl("uif_echo_dev_silver_mdm.mtl_item_revisions_b", { joins: [
        { leftTable: QUALIFIED_DRIVING, leftColumn: "INVENTORY_ITEM_ID",
          rightColumn: "INVENTORY_ITEM_ID" }] })];
    const col = scalar({ source: { table: "MTL_ITEM_REVISIONS_B", column: "REVISION", filter: null } });
    const sql = previewColumnSql(col, sources, DRIVING);
    expect(sql).toMatch(/FROM MTL_SYSTEM_ITEMS_B/i);
    expect(sql).toContain("LEFT JOIN mtl_item_revisions_b");
    expect(sql).not.toContain("attachment not defined");
  });

  it("EAV source pivots with max() under its row filter", () => {
    const col = scalar({ goldColumnName: "lineOfBusiness",
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR40",
                filter: "ATTR_GROUP_ID = 802" } });
    const sql = previewColumnSql(col, [], DRIVING);
    expect(sql).toContain("max(C_EXT_ATTR40)");
    expect(sql).toContain("ATTR_GROUP_ID = 802");
    expect(sql).not.toContain("attachment not defined");
  });
});

describe("scalar validation", () => {
  const ctx = { sources: [] as SourceDecl[], drivingTable: DRIVING };

  it("accepts the driving table (bare or qualified) without a join", () => {
    expect(columnProblems(scalar(), ctx)).toEqual([]);
    expect(columnProblems(scalar({ source: { table: QUALIFIED_DRIVING,
      column: "SEGMENT1", filter: null } }), ctx)).toEqual([]);
  });

  it("flags a non-driving source with no attachment", () => {
    const col = scalar({ source: { table: "MTL_SYSTEM_ITEMS_TL",
      column: "LONG_DESCRIPTION", filter: null } });
    expect(columnProblems(col, ctx).join()).toContain("not the driving table");
  });

  it("accepts a non-driving source once its attachment is structured", () => {
    const col = scalar({ source: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE",
      filter: null, join: { type: "left", on: [{ leftTable: DRIVING,
        leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" }] } } });
    expect(columnProblems(col, ctx)).toEqual([]);
  });

  it("flags a half-edited ON pair", () => {
    const col = scalar({ source: { table: "MTL_PARAMETERS", column: "X", filter: null,
      join: { type: "left", on: [{ leftTable: "", leftColumn: "", rightColumn: "Y" }] } } });
    expect(columnProblems(col, ctx).join()).toContain("attachment ON 1 is incomplete");
  });

  it("EAV sources also declare their entity-key join (UIFPE-104 — no exemptions)", () => {
    const bare = scalar({ source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
      column: "C_EXT_ATTR40", filter: "ATTR_GROUP_ID = 802" } });
    expect(columnProblems(bare, ctx).join()).toContain("not the driving table");
    const joined = scalar({ source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
      column: "C_EXT_ATTR40", filter: "ATTR_GROUP_ID = 802",
      join: { type: "left", on: [{ leftTable: DRIVING,
        leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" }] } } });
    expect(columnProblems(joined, ctx)).toEqual([]);
  });

  it("exempts tables declared in the source graph (any naming style)", () => {
    const col = scalar({ source: { table: "MTL_ITEM_REVISIONS_B", column: "REVISION", filter: null } });
    const withDecl = { drivingTable: DRIVING, sources: [decl(QUALIFIED_DRIVING),
      decl("uif_echo_dev_silver_mdm.mtl_item_revisions_b")] };
    expect(columnProblems(col, withDecl)).toEqual([]);
  });
});

// ── conditional: structured branches, per-branch transform/grain ────────────

const conditional = (): ConditionalColumn => ({
  goldColumnName: "sellingRestrictionCode", businessDescription: "restriction",
  kind: "conditional", dataType: "string",
  discriminator: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "ITEM_CATALOG_GROUP_ID" },
  channels: { event: true, message: true, api: true },
  branches: [
    { condition: "ELSE", source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
        column: "C_EXT_ATTR12", filter: "ATTR_GROUP_ID = 800" },
      transform: null, fallback: "returnNull", notes: null },
    { condition: "10002 (Digital Catalog)", source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
        column: "C_EXT_ATTR2", filter: "ATTR_GROUP_ID = 1414" },
      transform: "uppercase", fallback: "returnNull", notes: null },
  ], notes: null,
});

describe("conditions round-trip and rendering", () => {
  it("parses and reformats every canonical shape", () => {
    for (const raw of ["10002 (Digital Catalog)", "10002,10003 (bundles)", "ELSE"])
      expect(formatCondition(parseCondition(raw))).toBe(raw);
    // legacy prose degrades safely to ELSE
    expect(parseCondition("ELSE (<> 10002)").op).toBe("else");
  });

  it("renders equals / IN / complement deterministically", () => {
    const named = [parseCondition("10002,10003")];
    expect(branchWhere("D", parseCondition("10002 (x)"), named)).toBe("D = 10002");
    expect(branchWhere("D", parseCondition("10002,10003"), named)).toBe("D IN (10002, 10003)");
    expect(branchWhere("D", parseCondition("ELSE"), named)).toBe("D NOT IN (10002, 10003)");
    expect(branchWhere("D", parseCondition("ELSE"), [parseCondition("10002")]))
      .toBe("D <> 10002");
  });

  it("quotes non-numeric literals", () => {
    expect(branchWhere("D", parseCondition("US (states)"), [])).toBe("D = 'US'");
  });

  it("NEVER throws on half-built input — placeholders instead (empty condition once crashed the whole Rules step)", () => {
    expect(branchWhere("D", parseCondition(""), [])).toBe("D = {value}");
    expect(branchWhere("D", parseCondition("   "), [])).toBe("D = {value}");
    expect(branchWhere("D", { op: "in", values: [], label: null }, []))
      .toBe("D IN ({values})");
    expect(branchWhere("D", parseCondition("ELSE"), []))
      .toBe("D NOT IN ({other branches' values})");
  });
});

describe("conditional preview + validation", () => {
  it("renders branch CTEs with condition AND filter, grain and transform", () => {
    const sql = previewColumnSql(conditional(), [], DRIVING);
    expect(sql).toContain("WHERE ITEM_CATALOG_GROUP_ID <> 10002 AND ATTR_GROUP_ID = 800");
    expect(sql).toContain("WHERE ITEM_CATALOG_GROUP_ID = 10002 AND ATTR_GROUP_ID = 1414");
    // prose transforms are the pre-UIFPE-105 shape: surfaced as a deprecation
    // note, never silently dropped
    expect(sql).toContain("-- transform is FREE TEXT (deprecated) — restructure it: uppercase");
    expect(sql).toContain("COALESCE(");
  });

  it("validates branch completeness", () => {
    const col = conditional();
    col.branches[1].source.column = "";
    expect(columnProblems(col).join()).toContain("branch 2: source table and column are required");
  });
});

// ── Proliferate: row scopes (titleStatusCode pattern) ────────────────────────

const UK_SCOPE = { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE",
  op: "startsWith" as const, values: ["01"], label: "UK orgs (GBPE)" };
const NON_UK = { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE",
  op: "else" as const, values: [], label: "non-UK orgs" };
// The registry form: defined ONCE per mapping, referenced by name. The
// complement is DEFINED as the complement — the pair can never drift.
const ROW_GROUPS = {
  ukOrgs: { table: "MTL_PARAMETERS", column: "ORGANIZATION_CODE",
    op: "startsWith" as const, values: ["01"], description: "UK orgs (GBPE)" },
  nonUkOrgs: { complementOf: "ukOrgs", description: "everything else" },
};

const KEY_JOIN = { type: "left" as const, on: [{ leftTable: DRIVING,
  leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" }] };

const proliferated = (): ConditionalColumn => ({
  goldColumnName: "fulfillmentOrgDetails.titleStatusCode",
  businessDescription: "Title Status", kind: "conditional", dataType: "string",
  discriminator: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "ITEM_CATALOG_GROUP_ID" },
  channels: { event: true, message: true, api: true },
  branches: [
    { condition: "ELSE", source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
        column: "C_EXT_ATTR1", filter: "ATTR_GROUP_ID = 1171", join: KEY_JOIN },
      fallback: "returnNull", notes: null },
    { condition: "10002 (Digital)", scope: { group: "nonUkOrgs" },
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
                filter: "ATTR_GROUP_ID = 1408", join: KEY_JOIN },
      fallback: "returnNull", notes: null },
    { condition: "10002 (Digital)", scope: { group: "ukOrgs" },
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR1",
                filter: "ATTR_GROUP_ID = 1417", join: KEY_JOIN },
      fallback: "returnNull", notes: null },
  ], notes: null,
});

describe("Proliferate row scopes", () => {
  it("renders each scope op deterministically", () => {
    expect(scopeWhere(UK_SCOPE, [])).toBe("ORGANIZATION_CODE LIKE '01%'");
    expect(scopeWhere(NON_UK, [UK_SCOPE, NON_UK]))
      .toBe("NOT (ORGANIZATION_CODE LIKE '01%')");
    expect(scopeWhere({ column: "C", op: "equals", values: ["X"] }, [])).toBe("C = 'X'");
    expect(scopeWhere({ column: "C", op: "in", values: ["1", "2"] }, [])).toBe("C IN (1, 2)");
    expect(scopeLabel(UK_SCOPE)).toBe("UK orgs (GBPE)");
    expect(scopeLabel({ column: "C", op: "else", values: [] })).toBe("all remaining rows");
  });

  it("shared discriminator values dedupe in the ELSE complement", () => {
    const named = [parseCondition("10002 (Digital)"), parseCondition("10002 (Digital)")];
    expect(branchWhere("D", parseCondition("ELSE"), named)).toBe("D <> 10002");
  });

  it("resolves group references against the mapping's rowGroups registry", () => {
    const r = resolveScope({ group: "ukOrgs" }, ROW_GROUPS);
    expect(r).toMatchObject({ column: "ORGANIZATION_CODE", op: "startsWith",
                              values: ["01"], label: "ukOrgs" });
    expect(resolveScope({ group: "ghost" }, ROW_GROUPS).missing).toBe(true);
    expect(resolveScope(NON_UK, ROW_GROUPS)).toMatchObject({ op: "else" });
  });

  it("resolves a complement group AS its base's negation — never a restatement", () => {
    const r = resolveScope({ group: "nonUkOrgs" }, ROW_GROUPS);
    expect(r).toMatchObject({ column: "ORGANIZATION_CODE", op: "startsWith",
                              values: ["01"], not: true, label: "nonUkOrgs" });
    expect(scopeWhere(r, [])).toBe("NOT (ORGANIZATION_CODE LIKE '01%')");
    // a complement of a complement (or of a ghost) is unresolvable
    expect(resolveScope({ group: "nonUkOrgs" },
      { nonUkOrgs: { complementOf: "ghost" } }).missing).toBe(true);
  });

  it("preview fans scoped branches out via an apply-time CASE (matches product.sql)", () => {
    const sql = previewColumnSql(proliferated(), [], DRIVING, ROW_GROUPS);
    expect(sql).toContain("receives: nonUkOrgs");
    expect(sql).toContain("receives: ukOrgs");
    // both digital CTEs filter = 10002; legacy ELSE complements it once
    expect(sql).toContain("WHERE ITEM_CATALOG_GROUP_ID <> 10002 AND ATTR_GROUP_ID = 1171");
    expect(sql).toMatch(
      /COALESCE\(CASE WHEN ORGANIZATION_CODE LIKE '01%' THEN br3\S+ ELSE br2\S+ END, br1\S+\)/);
  });

  it("validates: scopes are complete, groups known, at most one else", () => {
    const ctx = { sources: [] as SourceDecl[], drivingTable: DRIVING, rowGroups: ROW_GROUPS };
    const col2 = proliferated();
    col2.branches[2].scope = { ...UK_SCOPE, values: [] };
    expect(columnProblems(col2, ctx).join()).toContain("scope needs at least one value");
    const col3 = proliferated();
    col3.branches[2].scope = { ...NON_UK };
    expect(columnProblems(col3, ctx).join()).toContain("only one branch can scope");
    const col4 = proliferated();
    col4.branches[2].scope = { group: "ghost" };
    expect(columnProblems(col4, ctx).join()).toContain("“ghost” is not defined");
    expect(columnProblems(proliferated(), ctx)).toEqual([]);
  });
});

// ── nested: multi-table arrays ───────────────────────────────────────────────

const nested = (): NestedColumn => ({
  goldColumnName: "revisionHistory", businessDescription: "revisions",
  kind: "nested", joinKey: "INVENTORY_ITEM_ID -> item", filter: null,
  channels: { event: false, message: false, api: true },
  fields: [
    { name: "revisionCode", type: "string", sortKey: true,
      source: { table: "MTL_ITEM_REVISIONS_B", column: "REVISION" } },
    { name: "itemNumber", type: "string", sortKey: false,
      source: { table: QUALIFIED_DRIVING, column: "SEGMENT1" } },
  ], notes: null,
});

describe("nested preview + validation", () => {
  const sources = [decl(QUALIFIED_DRIVING),
    decl("uif_echo_dev_silver_mdm.mtl_item_revisions_b", { joins: [
      { leftTable: QUALIFIED_DRIVING, leftColumn: "INVENTORY_ITEM_ID",
        rightColumn: "INVENTORY_ITEM_ID" }] })];

  it("qualifies fields per table and resolves the join from EITHER side", () => {
    const sql = previewColumnSql(nested(), sources, DRIVING);
    expect(sql).toContain("'revisionCode', mtl_item_revisions_b.REVISION");
    expect(sql).toContain("'itemNumber', mtl_system_items_b.SEGMENT1");
    expect(sql).toMatch(
      /LEFT JOIN mtl_system_items_b \S* ?ON mtl_item_revisions_b\.INVENTORY_ITEM_ID = mtl_system_items_b\.INVENTORY_ITEM_ID/);
  });

  it("allows multi-key sorts but never zero (UIFPE-262)", () => {
    // multi-key is the platform norm (marketingCopy sorts by three)
    const two = nested();
    two.fields[1].sortKey = true;
    expect(columnProblems(two).join()).not.toContain("sort key");
    const none = nested();
    for (const f of none.fields) f.sortKey = false;
    expect(columnProblems(none).join())
      .toContain("at least one field must be a sort key");
  });

  it("pins the pick's partition to the sort keys (UIFPE-262)", () => {
    const col = nested();
    col.pick = { partitionBy: [col.fields[1].name],
                 orderBy: [{ expr: "X DESC" }] };
    expect(columnProblems(col).join()).toContain("make it a sort key");
    col.fields[1].sortKey = true;
    expect(columnProblems(col).join()).not.toContain("make it a sort key");
  });

  it("flags a secondary field table that is not one of the task's sources (add flow)", () => {
    const col = nested();
    col.fields[1].source = { table: "SOME_OTHER_TABLE", column: "X" };
    const problems = columnProblems(col, { sources, drivingTable: DRIVING });
    expect(problems.join()).toContain("SOME_OTHER_TABLE");
    // UIFPE-219: the ask is REGISTRATION in Sources, not ON conditions —
    // that step no longer collects them (the attachment is column-level)
    expect(problems.join()).toContain("add it in Sources");
    expect(problems.join()).not.toContain("ON conditions");
  });
});

// ── source graph validation ──────────────────────────────────────────────────

describe("sourceProblems", () => {
  it("requires the first source to be the entity's registered primary table", () => {
    const out = sourceProblems([decl("MTL_PARAMETERS")], DRIVING);
    expect(out.join()).toContain("primary table (MTL_SYSTEM_ITEMS_B)");
    expect(sourceProblems([decl(QUALIFIED_DRIVING)], DRIVING)).toEqual([]);
  });

  it("demands NO joins — attachments are column-level since UIFPE-219", () => {
    // The Sources step registers tables only; how each attribute attaches is
    // declared and validated per column (columnProblems checks every ON
    // pair). A task-level join a pre-219 draft still carries is tolerated as
    // pre-fill data, never demanded.
    const tablesOnly = [decl(QUALIFIED_DRIVING), decl("X.Y")];
    expect(sourceProblems(tablesOnly, DRIVING)).toEqual([]);
    const legacyJoin = [decl(QUALIFIED_DRIVING), decl("X.Y", { joins: [
      { leftTable: "NOT_DECLARED", leftColumn: "A", rightColumn: "B" }] })];
    expect(sourceProblems(legacyJoin, DRIVING)).toEqual([]);
  });
});

// ── negative cases: hostile/malformed input must degrade, never crash ───────

describe("normalizeColumns (entry boundary for imports + AI drafts)", () => {
  it("fills the fields render code assumes — a channels-less column used to crash the wizard", () => {
    const [c] = normalizeColumns([{ goldColumnName: "brokenCol", kind: "scalar",
      dataType: "string", source: { table: "MTL_SYSTEM_ITEMS_B", column: "X" } }]);
    expect((c as ScalarColumn).channels).toEqual({ event: false, message: false, api: true });
    expect((c as ScalarColumn).nullable).toBe(true);
    expect((c as ScalarColumn).transform).toBeNull();
    expect((c as ScalarColumn).source.filter).toBeNull();
  });

  it("drops entries without a name or a recognizable kind", () => {
    expect(normalizeColumns([{ kind: "scalar" }, { goldColumnName: "x", kind: "banana" },
      null, "garbage"])).toEqual([]);
  });

  it("normalizes branch and field internals too", () => {
    const [c] = normalizeColumns([{ goldColumnName: "c", kind: "conditional",
      branches: [{ condition: "ELSE" }] }]) as ConditionalColumn[];
    expect(c.branches[0].fallback).toBe("returnNull");
    expect(c.branches[0].source.table).toBe("");
    const [n] = normalizeColumns([{ goldColumnName: "n", kind: "nested",
      fields: [{ name: "f" }] }]) as NestedColumn[];
    expect(n.fields[0].source).toEqual({ table: "", column: "" });
  });
});

describe("taskProblems (cross-column)", () => {
  it("flags in-task duplicate names and registered collisions", () => {
    const cols = [scalar({ goldColumnName: "dup" }), scalar({ goldColumnName: "dup" }),
                  scalar({ goldColumnName: "ppid" })];
    const out = taskProblems(cols, ["ppid"]);
    expect(out.join()).toContain('"dup" is defined 2 times');
    expect(out.join()).toContain('"ppid" already exists');
    expect(taskProblems([scalar()], [])).toEqual([]);
  });
});

describe("negative: duplicate branch conditions / nested field names", () => {
  it("two branches sharing a condition with the SAME scope are flagged", () => {
    const col = proliferated();
    col.branches[2].scope = { group: "nonUkOrgs" };  // now same as branch 1's
    expect(columnProblems(col).join()).toContain("share condition");
  });

  it("distinct scopes on a shared condition stay legal (the UK/non-UK pattern)", () => {
    expect(columnProblems(proliferated(), { sources: [], drivingTable: DRIVING,
      rowGroups: ROW_GROUPS })).toEqual([]);
  });

  it("duplicate nested field names are flagged", () => {
    const col = nested();
    col.fields[1].name = col.fields[0].name;
    expect(columnProblems(col).join()).toContain("is used 2 times");
  });
});

// ── Transform engine (UIFPE-105) ─────────────────────────────────────────────
// The two registered rules ARE the acceptance test: roundTo2dp must render the
// unit* columns' CAST(round(...)) and pearsonImageUrl must render the
// coverImage CASE exactly as product.sql hand-writes them.

const TRANSFORMS = {
  roundTo2dp: {
    steps: [{ op: "round" as const, precision: 2 },
            { op: "cast" as const, to: "DECIMAL(38,2)" }],
  },
  pearsonImageUrl: {
    cases: [
      { when: { op: "startsWith" as const, values: ["/A"] },
        then: { op: "prepend" as const,
                text: "https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a" } },
      { when: { op: "like" as const, values: ["/coverimage%", "/bigcovers%"] },
        then: { op: "prepend" as const, text: "https://images.pearson.com" } },
    ],
    else: "keep" as const,
  },
  damOnly: {
    cases: [{ when: { op: "startsWith" as const, values: ["/A"] },
              then: { op: "prepend" as const, text: "https://dam" } }],
    else: "null" as const,
  },
};

describe("transformSql rendering", () => {
  it("steps pipeline renders the unit* shape: CAST(round(x, 2) AS DECIMAL(38,2))", () => {
    expect(transformSql(TRANSFORMS.roundTo2dp, "items.UNIT_WEIGHT"))
      .toBe("CAST(round(items.UNIT_WEIGHT, 2) AS DECIMAL(38,2))");
  });

  it("cases render the coverImage CASE: substring guard, LIKE ORs, concat prepend, ELSE keep", () => {
    const sql = transformSql(TRANSFORMS.pearsonImageUrl, "x");
    expect(sql).toContain("WHEN substring(x, 1, 2) = '/A' THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a', x)");
    expect(sql).toContain("WHEN x LIKE '/coverimage%' OR x LIKE '/bigcovers%' THEN concat('https://images.pearson.com', x)");
    expect(sql).toContain("ELSE x");
  });

  it("else: null renders the strict HighRes variant", () => {
    expect(transformSql(TRANSFORMS.damOnly, "x")).toContain("ELSE NULL");
  });

  it("remaining ops: trim/upper/lower wrap, coalesceTo defaults, replace/append/literal/isNull", () => {
    expect(transformSql({ steps: [{ op: "trim" }, { op: "upper" }] }, "x"))
      .toBe("upper(trim(x))");
    expect(transformSql({ steps: [{ op: "coalesceTo", value: "N/A" }] }, "x"))
      .toBe("COALESCE(x, 'N/A')");
    expect(transformSql({ cases: [
      { when: { op: "isNull" }, then: { op: "literal", text: "unknown" } },
      { when: { op: "equals", values: ["a", "b"] }, then: { op: "replaceText", find: "a", replaceWith: "b" } },
      { when: { op: "in", values: ["1", "2"] }, then: { op: "append", text: "!" } },
    ] }, "x")).toBe(
      "CASE\n  WHEN x IS NULL THEN 'unknown'\n  WHEN x = 'a' OR x = 'b' THEN replace(x, 'a', 'b')\n  WHEN x IN ('1', '2') THEN concat(x, '!')\n  ELSE x\nEND");
  });

  it("sqlExpr substitutes {x} everywhere; literals escape quotes", () => {
    expect(transformSql({ sqlExpr: "date_format({x}, 'yyyy') || {x}" }, "c"))
      .toBe("date_format(c, 'yyyy') || c");
    expect(transformSql({ cases: [{ when: { op: "equals", values: ["it's"] },
      then: { op: "literal", text: "o'k" } }] }, "x"))
      .toContain("WHEN x = 'it''s' THEN 'o''k'");
  });

  it("steps wrap AFTER cases when combined", () => {
    const sql = transformSql({
      cases: [{ when: { op: "isNull" }, then: { op: "literal", text: "0" } }],
      steps: [{ op: "cast", to: "INT" }] }, "x");
    expect(sql.startsWith("CAST(CASE")).toBe(true);
    expect(sql.endsWith("END AS INT)")).toBe(true);
  });
});

describe("resolveTransform", () => {
  it("resolves null / prose / named / missing / inline", () => {
    expect(resolveTransform(null, TRANSFORMS)).toBeNull();
    expect(resolveTransform(undefined, TRANSFORMS)).toBeNull();
    expect(resolveTransform("round it", TRANSFORMS)).toEqual({ prose: "round it" });
    expect(resolveTransform({ rule: "roundTo2dp" }, TRANSFORMS))
      .toEqual({ def: TRANSFORMS.roundTo2dp, rule: "roundTo2dp" });
    expect(resolveTransform({ rule: "ghost" }, TRANSFORMS))
      .toEqual({ rule: "ghost", missing: true });
    expect(resolveTransform({ steps: [{ op: "trim" }] }, TRANSFORMS))
      .toEqual({ def: { steps: [{ op: "trim" }] } });
  });
});

describe("transform in previews", () => {
  it("scalar: a named rule wraps the source expression in the SELECT", () => {
    const col = scalar({ goldColumnName: "unitWeight",
      source: { table: DRIVING, column: "UNIT_WEIGHT", filter: null },
      transform: { rule: "roundTo2dp" } });
    expect(previewColumnSql(col, [], DRIVING, {}, TRANSFORMS))
      .toContain("SELECT CAST(round(mtl_system_items_b.UNIT_WEIGHT, 2) AS DECIMAL(38,2)) AS unitWeight");
  });

  it("conditional: the after-pick transform wraps the COALESCE — the coverImageLarge shape", () => {
    const col = conditional();
    (col as any).transform = { rule: "pearsonImageUrl" };
    const sql = previewColumnSql(col, [], DRIVING, {}, TRANSFORMS);
    // br2 (the named Digital branch) leads the pick; br1 (ELSE) closes it —
    // the compiler's named-first order, mirrored since UIFPE-106
    expect(sql).toMatch(/WHEN substring\(COALESCE\(br2_\w+\.\w+, br1_\w+\.\w+\), 1, 2\) = '\/A'/);
    expect(sql).toContain("THEN concat('https://images.pearson.com/i/79475d28-e560-442f-9d0c-afceca8a145a', COALESCE(");
  });

  it("a missing rule surfaces as a visible note, never silently dropped", () => {
    const col = scalar({ transform: { rule: "ghost" } });
    expect(previewColumnSql(col, [], DRIVING, {}, TRANSFORMS))
      .toContain('-- transform rule "ghost" is not defined in this mapping');
  });
});

describe("transformProblems validation", () => {
  const tctx = { sources: [], drivingTable: DRIVING, transforms: TRANSFORMS };

  it("accepts named rules and complete inline defs", () => {
    expect(columnProblems(scalar({ transform: { rule: "roundTo2dp" } }), tctx)).toEqual([]);
    expect(columnProblems(scalar({ transform: TRANSFORMS.pearsonImageUrl }), tctx)).toEqual([]);
  });

  it("flags prose, missing rules, and empty defs", () => {
    expect(columnProblems(scalar({ transform: "round it" }), tctx).join())
      .toContain("transform is free text");
    expect(columnProblems(scalar({ transform: { rule: "ghost" } }), tctx).join())
      .toContain("is not defined");
    expect(columnProblems(scalar({ transform: { cases: [], steps: [] } }), tctx).join())
      .toContain("no steps, cases, or SQL");
  });

  it("flags incomplete operands: round sans precision, case sans values, sqlExpr sans {x}", () => {
    expect(columnProblems(scalar({ transform: { steps: [{ op: "round" }] } }), tctx).join())
      .toContain("needs a precision");
    expect(columnProblems(scalar({ transform: { cases: [{ when: { op: "like", values: [] },
      then: { op: "keep" } }] } }), tctx).join())
      .toContain("needs at least one match value");
    expect(columnProblems(scalar({ transform: { cases: [{ when: { op: "isNull" },
      then: { op: "prepend" } }] } }), tctx).join())
      .toContain("needs text");
    expect(columnProblems(scalar({ transform: { sqlExpr: "upper(col)" } }), tctx).join())
      .toContain("must reference the source value as {x}");
  });

  it("normalizeColumns keeps contract shapes and nulls garbage", () => {
    const [c] = normalizeColumns([{ goldColumnName: "a", kind: "scalar",
      transform: { rule: "roundTo2dp" } }]) as ScalarColumn[];
    expect(c.transform).toEqual({ rule: "roundTo2dp" });
    const [d] = normalizeColumns([{ goldColumnName: "a", kind: "scalar",
      transform: 42 }]) as ScalarColumn[];
    expect(d.transform).toBeNull();
    const [e] = normalizeColumns([{ goldColumnName: "a", kind: "scalar",
      transform: { bogus: true } }]) as ScalarColumn[];
    expect(e.transform).toBeNull();
  });
});

// ── Gold table identity (UIFPE-107) ──────────────────────────────────────────

const ENTITY_KEY = [
  { goldColumn: "inventoryItemId", drivingColumn: "INVENTORY_ITEM_ID" },
  { goldColumn: "organizationId", drivingColumn: "ORGANIZATION_ID" },
];

describe("entityKey helpers", () => {
  it("entityKeyJoin prefills the full key against the driving table", () => {
    const j = entityKeyJoin(ENTITY_KEY, DRIVING)!;
    expect(j.type).toBe("left");
    expect(j.on).toEqual([
      { leftTable: DRIVING, leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" },
      { leftTable: DRIVING, leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" },
    ]);
    expect(entityKeyJoin([], DRIVING)).toBeNull();
    expect(entityKeyJoin(ENTITY_KEY, "")).toBeNull();
  });

  it("entityKeyProblems flags empties, bad names, and duplicates", () => {
    expect(entityKeyProblems(ENTITY_KEY)).toEqual([]);
    expect(entityKeyProblems([]).join()).toContain("at least one");
    expect(entityKeyProblems([{ goldColumn: "Bad_Name", drivingColumn: "X" }]).join())
      .toContain("camelCase");
    expect(entityKeyProblems([{ goldColumn: "a", drivingColumn: "" }]).join())
      .toContain("driving-table column");
    expect(entityKeyProblems([
      { goldColumn: "a", drivingColumn: "X" },
      { goldColumn: "a", drivingColumn: "Y" }]).join()).toContain("duplicate");
  });

  it("key columns must belong to the PRIMARY table when its catalog is known", () => {
    const cols = ["INVENTORY_ITEM_ID", "ORGANIZATION_ID"];
    expect(entityKeyProblems(ENTITY_KEY, cols)).toEqual([]);
    const bad = entityKeyProblems(
      [{ goldColumn: "orgCode", drivingColumn: "ORGANIZATION_CODE_FROM_PARAMS" }], cols);
    expect(bad.join()).toContain("not a column of the primary table");
    expect(bad.join()).toContain("grain-defining join");
    // unknown catalog (free-text driving table) — membership not enforceable
    expect(entityKeyProblems(ENTITY_KEY, [])).toEqual([]);
  });
});

describe("entity key in previews", () => {
  it("EAV pivot renders the key columns the source's join carries", () => {
    const col = scalar({ source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
      column: "C_EXT_ATTR1", filter: "ATTR_GROUP_ID = 802",
      join: { type: "left", on: [{ leftTable: DRIVING,
        leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" }] } } });
    const sql = previewColumnSql(col, [], DRIVING, {}, {}, ENTITY_KEY);
    expect(sql).toContain("SELECT INVENTORY_ITEM_ID,");
    expect(sql).toContain("GROUP BY INVENTORY_ITEM_ID");
    expect(sql).not.toContain("{entity_key}");
  });

  it("no declared join falls back to the FULL entity key; no entityKey keeps the placeholder", () => {
    const col = scalar({ source: { table: "EGO_MTL_SY_ITEMS_EXT_B",
      column: "C_EXT_ATTR1", filter: "ATTR_GROUP_ID = 802" } });
    expect(previewColumnSql(col, [], DRIVING, {}, {}, ENTITY_KEY))
      .toContain("GROUP BY INVENTORY_ITEM_ID, ORGANIZATION_ID");
    expect(previewColumnSql(col, [], DRIVING)).toContain("{entity_key}");
  });
});

// ── Kind conversion (UIFPE-108) ──────────────────────────────────────────────

describe("convertColumnKind", () => {
  it("scalar -> conditional is lossless: source becomes ELSE, transform becomes after-pick", () => {
    const col = scalar({ transform: { rule: "roundTo2dp" }, nullable: false });
    const { column, losses } = convertColumnKind(col, "conditional");
    expect(losses).toEqual([]);
    const c = column as ConditionalColumn;
    expect(c.kind).toBe("conditional");
    expect(c.branches).toHaveLength(1);
    expect(c.branches[0].condition).toBe("ELSE");
    expect(c.branches[0].source).toEqual(col.source);
    expect(c.transform).toEqual({ rule: "roundTo2dp" });
    expect(c.goldColumnName).toBe(col.goldColumnName);
  });

  it("conditional -> scalar keeps the ELSE branch by default and lists dropped branches", () => {
    const col = conditional();
    const { column, losses } = convertColumnKind(col, "scalar");
    const s = column as ScalarColumn;
    expect(s.kind).toBe("scalar");
    expect(losses.join()).toContain("branches dropped");
    expect(losses.join()).toContain("discriminator");
    // the ELSE branch survives
    const elseBranch = col.branches.find((b) => b.condition.toUpperCase().startsWith("ELSE"))
      ?? col.branches[0];
    expect(s.source).toEqual(elseBranch.source);
  });

  it("conditional -> scalar honors an explicit surviving branch", () => {
    const col = conditional();
    const { column } = convertColumnKind(col, "scalar", 1);
    expect((column as ScalarColumn).source).toEqual(col.branches[1].source);
  });

  it("scalar <-> nested round-trips the source through the value field", () => {
    const col = scalar({ dataType: "number", transform: { rule: "roundTo2dp" } });
    const { column: nestedCol, losses } = convertColumnKind(col, "nested");
    expect(losses).toEqual([]);
    const n = nestedCol as NestedColumn;
    expect(n.fields).toHaveLength(1);
    expect(n.fields[0]).toMatchObject({ name: "value", type: "number", sortKey: true });
    expect(n.fields[0].source?.table).toBe(col.source.table);
    expect(n.fields[0].transform).toEqual({ rule: "roundTo2dp" });

    const back = convertColumnKind(n, "scalar");
    const s = back.column as ScalarColumn;
    expect(s.source.table).toBe(col.source.table);
    expect(s.source.column).toBe(col.source.column);
    expect(s.dataType).toBe("number");
    expect(s.transform).toEqual({ rule: "roundTo2dp" });
  });

  it("nested -> scalar keeps the sort-key field and lists the dropped ones", () => {
    const col = nested();
    const { column, losses } = convertColumnKind(col, "scalar");
    const sortField = col.fields.find((f) => f.sortKey)!;
    expect((column as ScalarColumn).source.column).toBe(sortField.source?.column);
    expect(losses.join()).toContain("array fields dropped");
  });

  it("same kind is the identity", () => {
    const col = scalar();
    expect(convertColumnKind(col, "scalar")).toEqual({ column: col, losses: [] });
  });
});

describe("rememberedJoin — the task's latest word on a table's attachment", () => {
  const join = (rightCol: string) => ({
    type: "left" as const,
    on: [{ leftTable: "MTL_SYSTEM_ITEMS_B", leftColumn: "INVENTORY_ITEM_ID",
           rightColumn: rightCol }] });
  const scalarWith = (name: string, table: string, j?: ReturnType<typeof join>) => ({
    goldColumnName: name, businessDescription: "d", kind: "scalar",
    dataType: "string", source: { table, column: "X", filter: null,
                                  ...(j ? { join: j } : {}) },
    transform: null, channels: { event: true, message: true, api: true },
    nullable: true, notes: null,
  }) as never;

  it("returns the join another column already uses for the same table", () => {
    const cols = [scalarWith("a", "MTL_PARAMETERS", join("ORGANIZATION_ID"))];
    const got = rememberedJoin(cols, "MTL_PARAMETERS");
    expect(got?.fromColumn).toBe("a");
    expect(got?.join.on[0].rightColumn).toBe("ORGANIZATION_ID");
  });

  it("matches by bare table name across databases, and the LATEST wins", () => {
    const cols = [
      scalarWith("a", "MTL_PARAMETERS", join("OLD_COL")),
      scalarWith("b", "uif_echo_{env}_silver_mdm.mtl_parameters", join("NEW_COL")),
    ];
    const got = rememberedJoin(cols, "MTL_PARAMETERS");
    expect(got?.fromColumn).toBe("b");
    expect(got?.join.on[0].rightColumn).toBe("NEW_COL");
  });

  it("returns a COPY — editing the prefill must not mutate the source column", () => {
    const cols = [scalarWith("a", "MTL_PARAMETERS", join("ORGANIZATION_ID"))];
    const got = rememberedJoin(cols, "MTL_PARAMETERS")!;
    got.join.on[0].rightColumn = "EDITED";
    expect((cols[0] as { source: { join: { on: { rightColumn: string }[] } } })
      .source.join.on[0].rightColumn).toBe("ORGANIZATION_ID");
  });

  it("stays silent for tables no column attached, and for joinless reads", () => {
    expect(rememberedJoin([scalarWith("a", "MTL_PARAMETERS")], "MTL_PARAMETERS"))
      .toBeNull();
    expect(rememberedJoin([], "ANY")).toBeNull();
  });
});
