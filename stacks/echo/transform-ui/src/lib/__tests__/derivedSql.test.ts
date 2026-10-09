// The browser's advisory mirror of the derivation battery (UIFPE-219).
// Each case here corresponds to a refusal derivation_chain.py enforces for
// real — the mirror may stay silent on a maybe-fine query, but everything it
// DOES flag must be something the battery refuses, or authors learn to
// ignore it.
import { describe, it, expect } from "vitest";
import {
  derivedSqlProblems, referencedTables, stripNoise, emptyDerivation,
} from "../derivedSql";
import type { MappingColumn, SourceDecl } from "../types";
import { convertColumnKind } from "../types";

const col = (name = "parentProduct") =>
  ({ goldColumnName: name, kind: "scalar" } as unknown as MappingColumn);
const SOURCES: SourceDecl[] = [
  { table: "EGO_MTL_SY_ITEMS_EXT_B" }, { table: "MTL_SYSTEM_ITEMS_B" },
  { table: "silver_product.MTL_RELATED_ITEMS" },
] as SourceDecl[];

const VIEW = {
  aliases: new Set(["ITEMS", "ITEMS_EXT"]),
  aliasByTable: new Map([["MTL_SYSTEM_ITEMS_B", "items"],
                         ["EGO_MTL_SY_ITEMS_EXT_B", "items_ext"]]),
};

const GOOD = {
  sql: "SELECT INVENTORY_ITEM_ID, min(C_EXT_ATTR7) AS parentProduct\n"
    + "FROM EGO_MTL_SY_ITEMS_EXT_B\nGROUP BY INVENTORY_ITEM_ID",
  keyedBy: [{ queryColumn: "INVENTORY_ITEM_ID", entityColumn: "INVENTORY_ITEM_ID" }],
  valueColumn: "parentProduct",
};

describe("derivedSqlProblems — mirrors the battery's refusals", () => {
  it("passes the well-formed contract", () => {
    expect(derivedSqlProblems(col(), GOOD, SOURCES)).toEqual([]);
  });

  it("refuses a struct member, an empty query, a trailing semicolon", () => {
    expect(derivedSqlProblems(col("dimensions.height"), GOOD, SOURCES)[0])
      .toContain("struct member");
    expect(derivedSqlProblems(col(), { ...GOOD, sql: "  " }, SOURCES)[0])
      .toContain("empty");
    expect(derivedSqlProblems(col(), { ...GOOD, sql: GOOD.sql + ";" }, SOURCES)
      .join(" ")).toContain("trailing ';'");
  });

  it("refuses statement lists, writes, and non-SELECT openers", () => {
    const two = { ...GOOD, sql: "SELECT 1 FROM MTL_SYSTEM_ITEMS_B; SELECT 2" };
    expect(derivedSqlProblems(col(), two, SOURCES).join(" "))
      .toContain("one statement");
    const del = { ...GOOD, sql: "DELETE FROM EGO_MTL_SY_ITEMS_EXT_B" };
    expect(derivedSqlProblems(col(), del, SOURCES).join(" "))
      .toContain("read-only");
    // but UPDATE inside an identifier is NOT a write — no false alarm
    const okCol = { ...GOOD, sql: GOOD.sql.replace(
      "min(C_EXT_ATTR7)", "max(LAST_UPDATE_DATE)") };
    expect(derivedSqlProblems(col(), okCol, SOURCES).join(" "))
      .not.toContain("read-only");
  });

  it("refuses run-varying functions, wherever they hide", () => {
    for (const bad of ["uuid()", "current_timestamp", "now()",
                       "unix_timestamp( )", "reflect('java.lang.Math','random')"]) {
      const p = derivedSqlProblems(col(),
        { ...GOOD, sql: GOOD.sql.replace("min(C_EXT_ATTR7)", `min(${bad})`) },
        SOURCES);
      expect(p.join(" "), bad).toContain("stable");
    }
    // …but not inside comments or strings
    const commented = { ...GOOD, sql: GOOD.sql + "\n-- uuid() explained here" };
    expect(derivedSqlProblems(col(), commented, SOURCES)).toEqual([]);
  });

  it("requires collects to state their ordering", () => {
    const bag = { ...GOOD, sql: GOOD.sql.replace(
      "min(C_EXT_ATTR7)", "collect_list(C_EXT_ATTR7)") };
    expect(derivedSqlProblems(col(), bag, SOURCES).join(" "))
      .toContain("array_sort");
    const sorted = { ...GOOD, sql: GOOD.sql.replace(
      "min(C_EXT_ATTR7)", "array_sort(collect_list(C_EXT_ATTR7))") };
    expect(derivedSqlProblems(col(), sorted, SOURCES)).toEqual([]);
  });

  it("accepts the entity's registered manifest aliases — found live: the "
     + "REGISTERED parentProduct query reads items_ext and showed red", () => {
    const aliased = { ...GOOD, sql: GOOD.sql.replace(
      "EGO_MTL_SY_ITEMS_EXT_B", "items_ext") };
    expect(derivedSqlProblems(col(), aliased, SOURCES).join(" "))
      .toContain("items_ext");             // without the aliases: flagged
    expect(derivedSqlProblems(col(), aliased, SOURCES, VIEW)).toEqual([]);
  });

  it("names the alias to use when a query reads the physical table a "
     + "registered alias already covers", () => {
    // the battery only accepts alias names for manifest sources — a bare
    // physical-table read is refused there, so the panel says the fix
    const p = derivedSqlProblems(col(), GOOD, SOURCES, VIEW);
    expect(p.join(" ")).toContain("reads EGO_MTL_SY_ITEMS_EXT_B as 'items_ext'");
  });

  it("never flags FROM inside function calls — extract, trim, VALUES", () => {
    // found adversarially: these are battery-clean Spark the regex flagged
    const cases = [
      GOOD.sql.replace("min(C_EXT_ATTR7)", "max(extract(YEAR FROM CREATION_DATE))"),
      GOOD.sql.replace("min(C_EXT_ATTR7)", "trim(BOTH 'x' FROM C_EXT_ATTR7)"),
    ];
    for (const sql of cases)
      expect(derivedSqlProblems(col(), { ...GOOD, sql }, SOURCES), sql)
        .toEqual([]);
    expect(referencedTables("SELECT * FROM VALUES (1) AS v(x)")).toEqual([]);
  });

  it("recognizes a CTE that declares a column list", () => {
    const sql = "WITH t(iid, parentProduct) AS (SELECT INVENTORY_ITEM_ID, "
      + "min(C_EXT_ATTR7) FROM EGO_MTL_SY_ITEMS_EXT_B GROUP BY 1) "
      + "SELECT iid, parentProduct FROM t";
    expect(derivedSqlProblems(col(),
      { ...GOOD, keyedBy: [{ queryColumn: "iid", entityColumn: "INVENTORY_ITEM_ID" }], sql },
      SOURCES)).toEqual([]);
  });

  it("reads contract columns through backtick quotes", () => {
    // idiomatic Spark quoting must not blank the aliases the presence
    // check looks for
    const sql = "SELECT INVENTORY_ITEM_ID AS `iid`, min(C_EXT_ATTR7) AS "
      + "`parentProduct` FROM EGO_MTL_SY_ITEMS_EXT_B GROUP BY 1";
    expect(derivedSqlProblems(col(),
      { ...GOOD, keyedBy: [{ queryColumn: "iid", entityColumn: "INVENTORY_ITEM_ID" }], sql },
      SOURCES)).toEqual([]);
  });

  it("matches contract columns case-sensitively, like the battery", () => {
    const sql = GOOD.sql.replace("AS parentProduct", "AS PARENTPRODUCT");
    expect(derivedSqlProblems(col(), { ...GOOD, sql }, SOURCES).join(" "))
      .toContain("parentProduct");
  });

  it("accepts a parenthesized set-operation query", () => {
    const sql = "(SELECT INVENTORY_ITEM_ID, C_EXT_ATTR7 AS parentProduct "
      + "FROM EGO_MTL_SY_ITEMS_EXT_B)\nUNION\n(SELECT INVENTORY_ITEM_ID, "
      + "C_EXT_ATTR8 FROM EGO_MTL_SY_ITEMS_EXT_B)";
    expect(derivedSqlProblems(col(), { ...GOOD, sql }, SOURCES)).toEqual([]);
  });

  it("holds the sources fence: undeclared and db-qualified reads named", () => {
    const stranger = { ...GOOD, sql: GOOD.sql.replace(
      "EGO_MTL_SY_ITEMS_EXT_B", "SOME_OTHER_TABLE") };
    expect(derivedSqlProblems(col(), stranger, SOURCES).join(" "))
      .toContain("Sources step");
    const qualified = { ...GOOD, sql: GOOD.sql.replace(
      "EGO_MTL_SY_ITEMS_EXT_B", "silver_product.EGO_MTL_SY_ITEMS_EXT_B") };
    expect(derivedSqlProblems(col(), qualified, SOURCES).join(" "))
      .toContain("bare registered names");
    // the task's own db-qualified source still matches by bare name
    const related = { ...GOOD, sql: GOOD.sql.replace(
      "EGO_MTL_SY_ITEMS_EXT_B", "MTL_RELATED_ITEMS") };
    expect(derivedSqlProblems(col(), related, SOURCES)).toEqual([]);
  });

  it("holds the contract: key pairs complete and visible, value visible", () => {
    expect(derivedSqlProblems(col(), { ...GOOD, keyedBy: [] }, SOURCES)
      .join(" ")).toContain("keyedBy");
    const half = { ...GOOD, keyedBy: [{ queryColumn: "", entityColumn: "X" }] };
    expect(derivedSqlProblems(col(), half, SOURCES).join(" "))
      .toContain("both halves");
    const missing = { ...GOOD,
      keyedBy: [{ queryColumn: "PRODUCT_KEY", entityColumn: "INVENTORY_ITEM_ID" }] };
    expect(derivedSqlProblems(col(), missing, SOURCES).join(" "))
      .toContain("PRODUCT_KEY");
    const badValue = { ...GOOD, valueColumn: "somethingElse" };
    expect(derivedSqlProblems(col(), badValue, SOURCES).join(" "))
      .toContain("somethingElse");
  });
});

describe("referencedTables", () => {
  it("reads FROM and JOINs, skips the query's own CTEs", () => {
    const sql = `WITH ranked AS (SELECT * FROM MTL_RELATED_ITEMS),
      best AS (SELECT * FROM ranked JOIN MTL_SYSTEM_ITEMS_B USING (X))
      SELECT * FROM best`;
    expect(referencedTables(sql).sort())
      .toEqual(["MTL_RELATED_ITEMS", "MTL_SYSTEM_ITEMS_B"]);
  });

  it("never reads names out of comments or strings", () => {
    const sql = `SELECT 'FROM FAKE_TABLE' AS note -- FROM GHOST\nFROM REAL_T`;
    expect(referencedTables(sql)).toEqual(["REAL_T"]);
  });
});

describe("stripNoise", () => {
  it("blanks comments and every quote style", () => {
    expect(stripNoise("a 'x;y' `q` \"z\" -- tail\nb /* uuid() */ c"))
      .not.toMatch(/x;y|tail|uuid|q|z/);
  });

  it("never mistakes '--' inside a string for a comment", () => {
    // found adversarially: sequential replaces let `LIKE '%--%'` eat its
    // own closing quote, false-flagging the NEXT literal as a write —
    // and let a string mask a real ';' + DELETE
    const legit = { ...GOOD, sql: GOOD.sql
      + "\nHAVING min(C_EXT_ATTR7) LIKE '%--%'"
      + "\n   AND max(C_EXT_ATTR8) <> 'delete after review'" };
    expect(derivedSqlProblems(col(), legit, SOURCES)).toEqual([]);
    const masked = { ...GOOD, sql: "SELECT '--' AS a; DELETE FROM EGO_MTL_SY_ITEMS_EXT_B" };
    expect(derivedSqlProblems(col(), masked, SOURCES).length)
      .toBeGreaterThan(0);
  });
});

describe("emptyDerivation", () => {
  it("pre-fills the key contract from the entity key", () => {
    const b = emptyDerivation("parentProduct",
      [{ drivingColumn: "INVENTORY_ITEM_ID" },
       { drivingColumn: "ORGANIZATION_ID" }]);
    expect(b.keyedBy).toEqual([
      { queryColumn: "INVENTORY_ITEM_ID", entityColumn: "INVENTORY_ITEM_ID" },
      { queryColumn: "ORGANIZATION_ID", entityColumn: "ORGANIZATION_ID" }]);
    expect(b.valueColumn).toBe("parentProduct");
    expect(b.sql).toBe("");
  });
});
describe("kind conversion keeps the derivation", () => {
  it("survives scalar -> conditional -> nested — the query is HOW the value "
     + "is reached; kind is only its shape", () => {
    // found adversarially: one click on the kind button silently destroyed
    // a hand-written query, with no confirmation and no loss listed
    const scalar = {
      goldColumnName: "parentProduct", businessDescription: "x",
      kind: "scalar", dataType: "string",
      source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR7", filter: null },
      transform: null, channels: { event: true, message: true, api: true },
      nullable: true, notes: null, derivation: GOOD,
    } as unknown as MappingColumn;
    const cond = convertColumnKind(scalar, "conditional").column;
    expect(cond.derivation).toEqual(GOOD);
    const nested = convertColumnKind(cond, "nested").column;
    expect(nested.derivation).toEqual(GOOD);
    const back = convertColumnKind(nested, "scalar").column;
    expect(back.derivation).toEqual(GOOD);
  });
});
