// The one-journey pieces (UIFPE-219): registry-derived domains, the Sources
// card model, new-table artifacts, and draft migration across the step
// renumbering. Checked against production data where it exists (the
// registered product mapping), the sourceGrain.test.ts posture.
//
// That posture has a CI cost, paid by stacks/echo/ci-watch: these assertions
// move when a DOMAIN moves, and stacks-ci.yml selects its matrix per stack, so
// without that declaration a domain change never runs this file (UIFPE-738).
// Two tests below had been failing on develop for exactly that reason. Expect
// to update them when a domain lands, and prefer an UNREGISTERED target for
// anything testing mechanics rather than a specific entity.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { SETTLED_DOMAINS, REGISTERED } from "../repo";
import { tableSummaries } from "../sourceGrain";
import { newTableArtifacts } from "../newTableArtifacts";
import { seedFromImport } from "../entryMethods";
import { saveDraft, loadDraft, clearDraft } from "../drafts";
import { registeredSilverTables, sourceStanding } from "../manifests";
import { qualifyTable } from "../silverCatalog";
import { pipelineFor, PIPELINES } from "../pipelines";
import {
  COLUMNS_STEP, resolveEntry, stepGates, unfinishedBefore,
} from "../wizardFlow";
import { emptyWizard } from "../types";

describe("registeredSilverTables — runtime sources from the manifests", () => {
  it("knows product's real sources and excludes the BOM placeholders", () => {
    const t = registeredSilverTables("product");
    expect(t.has("MTL_SYSTEM_ITEMS_B")).toBe(true);
    expect(t.has("EGO_MTL_SY_ITEMS_EXT_B")).toBe(true);
    // the BOM tables were the "not wired in yet" placeholders until their
    // Silver sources registered (UIFPE-269, 2026-09-07) — now runtime sources
    expect(t.has("BOM_COMPONENTS_B")).toBe(true);
    // the pill still has something to key on: a table only the mapping names
    expect(t.has("EGO_ATTR_GROUPS_V")).toBe(false);
  });

  it("a domain without a manifest reports no sources (draft posture)", () => {
    // price gained a manifest with UIFPE-263; sku is still schema-less
    expect(registeredSilverTables("sku").size).toBe(0);
    expect(registeredSilverTables("price").size).toBeGreaterThan(0);
  });
});

describe("SETTLED_DOMAINS derives from the repository", () => {
  it("carries every domain folder — the stale-at-seven constant is gone", () => {
    // the next domain appears here the moment its folder is stamped, with no
    // list to remember to update — and one that LEAVES disappears the same
    // way: beacon moved to stacks/ (ADR-0010, 2026-08-31), so thirteen
    expect(SETTLED_DOMAINS.length).toBeGreaterThanOrEqual(13);
    for (const d of ["product", "program", "price", "stock", "sku", "orders"])
      expect(SETTLED_DOMAINS).toContain(d);
    expect(SETTLED_DOMAINS).not.toContain("assessment"); // never was a domain
    expect(SETTLED_DOMAINS).not.toContain("beacon");     // a stack, not a domain
    expect(SETTLED_DOMAINS).not.toContain("_domain-template");
  });
});

describe("tableSummaries — the Sources card model", () => {
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;
  const summaries = tableSummaries({
    drivingTable: product.drivingTable,
    entityKey: product.entityKey,
    columns: product.columns,
  });

  it("one card per table, primary first, with a plain sentence each", () => {
    expect(summaries[0].primary).toBe(true);
    expect(summaries[0].shortName.toUpperCase()).toBe("MTL_SYSTEM_ITEMS_B");
    expect(summaries[0].sentence).toContain("Decides which rows exist");
    for (const t of summaries) {
      expect(t.sentence.length).toBeGreaterThan(20);
      expect(t.usedBy).toBeGreaterThan(0);
    }
  });

  it("a table read several ways gets the mixed sentence and the odd detail", () => {
    const ext = summaries.find((t) =>
      t.shortName.toUpperCase() === "EGO_MTL_SY_ITEMS_EXT_B")!;
    expect(ext.sentence).toContain("more than one way");
    expect(ext.odd.length).toBeGreaterThan(1);
  });

  it("never leaks framework vocabulary into the sentences", () => {
    for (const t of summaries)
      expect(t.sentence).not.toMatch(/\bgrain\b|\bEAV\b|\battachment\b|key reach/i);
  });
});

describe("newTableArtifacts emits a VALID Draft-07 schema", () => {
  // the verification round's blocker: UI data types (date/timestamp/float)
  // are not JSON Schema types, and emitting them verbatim produced a schema
  // the repo's own CI validator rejects. The encoding must mirror
  // compile_intake_delta.py's master_prop().
  const col = (over: Record<string, unknown>) => ({
    goldColumnName: "c", businessDescription: "d", kind: "scalar" as const,
    dataType: "string" as const, nullable: true, transform: null,
    source: { table: "T", column: "C", filter: null },
    channels: { event: false, message: false, api: true }, notes: null,
    ...over,
  });
  const propOf = (over: Record<string, unknown>) => {
    const a = newTableArtifacts({
      ...emptyWizard(), domain: "orders", destinationTable: "gold_orders.o",
      isNewTable: true,
      entityKey: [{ goldColumn: "c", drivingColumn: "C" }],
      columns: [col(over) as never],
    });
    return JSON.parse(a.masterJson).properties.c;
  };
  const JSON_TYPES = new Set(["string", "integer", "number", "boolean",
                              "array", "object", "null"]);
  const typesOf = (p: { type: string | string[] }) =>
    Array.isArray(p.type) ? p.type : [p.type];

  it("translates every UI data type to a real JSON type", () => {
    for (const t of ["string", "integer", "number", "float", "boolean",
                     "date", "timestamp"] as const) {
      const p = propOf({ dataType: t });
      for (const j of typesOf(p))
        expect(JSON_TYPES.has(j), `${t} -> ${j}`).toBe(true);
    }
    // and keeps the date/time intent the JSON type erases
    expect(propOf({ dataType: "date" }).format).toBe("date");
    expect(propOf({ dataType: "timestamp" }).format).toBe("date-time");
    expect(propOf({ dataType: "float" }).type).toEqual(["number", "null"]);
  });

  it("mirrors the compiler's nullability and array encodings", () => {
    expect(propOf({ nullable: false }).type).toBe("string");
    expect(propOf({ nullable: true }).type).toEqual(["string", "null"]);
    // ONE field -> value array; several -> struct array (master_prop)
    const value = propOf({ kind: "nested", joinKey: "K", filter: null,
      fields: [{ name: "value", type: "string", sortKey: true,
                 source: { table: "T", column: "C" } }] });
    expect(value.type).toEqual(["array", "null"]);
    expect(value.items).toEqual({ type: "string" });
    const struct = propOf({ kind: "nested", joinKey: "K", filter: null,
      fields: [
        { name: "a", type: "string", sortKey: true, source: { table: "T", column: "A" } },
        { name: "b", type: "integer", sortKey: false, source: { table: "T", column: "B" } }] });
    expect(struct.items.type).toBe("object");
    expect(struct.items.properties.b.type).toEqual(["integer", "null"]);
  });

  it("refuses to look finished when the table has no key", () => {
    const a = newTableArtifacts({
      ...emptyWizard(), domain: "orders", destinationTable: "gold_orders.o",
      isNewTable: true, entityKey: [], columns: [col({}) as never],
    });
    expect(a.problems.join()).toContain("no key declared");
  });

  it("leaves struct MEMBERS out of the schema's top-level properties", () => {
    const a = newTableArtifacts({
      ...emptyWizard(), domain: "orders", destinationTable: "gold_orders.o",
      isNewTable: true,
      entityKey: [{ goldColumn: "c", drivingColumn: "C" }],
      columns: [col({}) as never, col({ goldColumnName: "parent.member" }) as never],
    });
    expect(Object.keys(JSON.parse(a.masterJson).properties)).toEqual(["c"]);
  });
});

describe("newTableArtifacts — the folded setup capability", () => {
  const state = {
    ...emptyWizard(),
    domain: "orders",
    destinationTable: "gold_orders.order",
    isNewTable: true,
    entityKey: [{ goldColumn: "orderId", drivingColumn: "ORDER_ID" }],
    columns: [
      { goldColumnName: "orderId", businessDescription: "Order id",
        kind: "scalar" as const, dataType: "integer" as const, nullable: false,
        source: { table: "OE_ORDER_HEADERS_ALL", column: "ORDER_ID", filter: null },
        transform: null,
        channels: { event: true, message: true, api: true }, notes: null },
    ],
  };

  it("generates a DRAFT master.json typed from the task's columns", () => {
    const a = newTableArtifacts(state);
    const master = JSON.parse(a.masterJson);
    expect(master.metadata.lifecycle).toBe("draft");
    expect(master.metadata.primaryKey)
      .toEqual([{ goldColumn: "orderId", drivingColumn: "ORDER_ID" }]);
    expect(master.required).toEqual(["orderId"]);
    expect(master.properties.orderId.type).toBe("integer"); // nullable: false
    expect(master.properties.orderId["x-channels"])
      .toEqual(["event", "message", "api"]);
    expect(a.paths.master).toContain("domains/orders/");
    expect(a.missingKeyColumns).toEqual([]);
  });

  it("names the key columns the task has not declared yet", () => {
    const a = newTableArtifacts({ ...state, columns: [] });
    expect(a.missingKeyColumns).toEqual(["orderId"]);
  });
});

describe("draft migration across the step renumbering", () => {
  const store = new Map<string, string>();
  beforeEach(() => {
    store.clear();
    (globalThis as any).localStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    };
  });

  it("a pre-219 draft's step index shifts down one (Method step removed)", () => {
    // simulate what the old app wrote: no version field, old numbering
    store.set("transform.draft.v1", JSON.stringify({
      state: emptyWizard(), step: 3, savedAt: "2026-08-20T10:00:00Z",
    }));
    expect(loadDraft()!.step).toBe(2); // old Rules(3) -> new Columns(2)
  });

  it("a current draft resumes exactly where it stopped", () => {
    saveDraft(emptyWizard(), 3);
    expect(loadDraft()!.step).toBe(3);
    clearDraft();
    expect(loadDraft()).toBeNull();
  });
});

// ── import intent (the verification round's second blocker) ────────────────

describe("seedFromImport derives intent instead of assuming 'add'", () => {
  const file = (obj: unknown) =>
    ({ text: async () => JSON.stringify(obj) } as File);
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;

  it("imports the registered mapping as a MODIFY, not a dead-end add", async () => {
    // every column already exists, so as an 'add' the Columns gate would
    // flag all 62 with "already exists on the destination table" and Next
    // could never enable — the import would be unusable
    const s = await seedFromImport(file({
      domain: "product", table: product.table,
      drivingTable: product.drivingTable, columns: product.columns,
    }));
    expect(s.intent).toBe("modify");
    expect(s.targetColumn).toBe(product.columns[0].goldColumnName);
    expect(s.columns.length).toBe(product.columns.length);
  });

  it("round-trips a downloaded modify envelope with its refresh plan", async () => {
    const col = product.columns[0];
    const s = await seedFromImport(file({
      task: "T", intent: "modify",
      refresh: { historical: false, mode: "silent" },
      intake: { domain: "product", table: product.table,
                drivingTable: product.drivingTable, columns: [col] },
    }));
    expect(s.intent).toBe("modify");
    expect(s.refreshHistorical).toBe(false);
    expect(s.refreshMode).toBe("silent");
  });

  it("treats an all-new mapping as an add", async () => {
    const s = await seedFromImport(file({
      domain: "product", table: product.table,
      drivingTable: product.drivingTable,
      columns: [{ ...product.columns[0], goldColumnName: "brandNewThing" }],
    }));
    expect(s.intent).toBe("add");
    expect(s.targetColumn).toBeNull();
  });

  it("REFUSES a file mixing new and existing attributes, naming the clash", async () => {
    // one task carries one intent: as an add the existing column dead-ends,
    // as a modify the compiler would edit an entry that is not there
    await expect(seedFromImport(file({
      domain: "product", table: product.table,
      drivingTable: product.drivingTable,
      columns: [product.columns[0],
                { ...product.columns[0], goldColumnName: "brandNewThing" }],
    }))).rejects.toThrow(/mixes new and existing/);
  });
});

// ── entry resolution: the draft must survive every re-entry ────────────────
// The verification round's data-loss blocker: the wizard SAVES the state it
// mounts with, so any entry that seeds a fresh task overwrites the draft.
// A refresh, a browser Forward onto the entry URL, or a dead bookmark must
// therefore RESTORE, never reset.

describe("resolveEntry", () => {
  const P = (s = "") => new URLSearchParams(s);
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;
  const midFlight = (over: Partial<ReturnType<typeof emptyWizard>> = {}) => ({
    state: { ...emptyWizard(), taskName: "MID_FLIGHT",
             domain: "product", destinationTable: product.table,
             columns: [structuredClone(product.columns[0])], ...over },
    step: 2, savedAt: "2026-08-27T10:00:00Z", v: 2,
  });

  it("restores the draft on a paramless entry (F5 on /transform/new)", () => {
    const got = resolveEntry(P(), null, midFlight());
    expect(got.state.taskName).toBe("MID_FLIGHT");
    expect(got.step).toBe(2);
  });

  it("restores on ?resume=1 and on a stale ?modify deep-link", () => {
    expect(resolveEntry(P("resume=1"), null, midFlight()).state.taskName)
      .toBe("MID_FLIGHT");
    // the attribute was renamed or removed: the seed cannot be built, and
    // the draft must not be reset over
    const stale = resolveEntry(
      P(`modify=${product.table}|noSuchColumnAnyMore`), null, midFlight());
    expect(stale.state.taskName).toBe("MID_FLIGHT");
  });

  it("keeps mid-flight work when Forward lands back on ?new=1", () => {
    const draft = midFlight({ isNewTable: true, destinationTable: "gold_x.y" });
    const got = resolveEntry(P("new=1"), null, draft);
    expect(got.state.taskName).toBe("MID_FLIGHT");
  });

  it("keeps mid-flight work when F5 lands back on the SAME ?modify", () => {
    const col = product.columns[0].goldColumnName;
    const draft = midFlight({ targetColumn: col, intent: "modify" });
    const got = resolveEntry(P(`modify=${product.table}|${col}`), null, draft);
    expect(got.state.taskName).toBe("MID_FLIGHT");
  });

  it("still seeds a genuinely new task when no draft is in the way", () => {
    // the home page clears the draft before every deliberate start
    const fresh = resolveEntry(P("new=1"), null, null);
    expect(fresh.state.isNewTable).toBe(true);
    expect(fresh.step).toBe(0);
    const col = product.columns[0].goldColumnName;
    const mod = resolveEntry(P(`modify=${product.table}|${col}`), null, null);
    expect(mod.state.intent).toBe("modify");
    expect(mod.state.targetColumn).toBe(col);
    expect(mod.step).toBe(COLUMNS_STEP);
  });

  it("takes a handed-over seed without needing storage at all", () => {
    const seed = { ...emptyWizard(), taskName: "IMPORTED", domain: "product" };
    const got = resolveEntry(P(), { seed, step: COLUMNS_STEP }, null);
    expect(got.state.taskName).toBe("IMPORTED");
    expect(got.step).toBe(COLUMNS_STEP);
  });
});

// ── step gates: a seeded task cannot skip a step it never walked ───────────

describe("stepGates + unfinishedBefore", () => {
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;

  it("flags the Gold-table step of a new-table import with no key", () => {
    // lands at Columns (step 2) with columns but no key declared: the old
    // per-step check let it walk to Review and submit a master.json whose
    // primaryKey and required lists were silently empty
    const state = { ...emptyWizard(), domain: "orders", isNewTable: true,
      destinationTable: "gold_orders.order", drivingTable: null,
      entityKey: [], sources: [], columns: [structuredClone(product.columns[0])] };
    const gates = stepGates(state);
    expect(gates[0]).toBe(false);
    expect(unfinishedBefore(gates, COLUMNS_STEP)).toBe(0);
  });

  it("passes a complete registered modify task", () => {
    const col = structuredClone(product.columns.find((c) =>
      c.kind === "scalar" && !c.goldColumnName.includes("."))!);
    const state = { ...emptyWizard(), domain: "product", intent: "modify" as const,
      destinationTable: product.table, drivingTable: product.drivingTable,
      entityKey: product.entityKey, targetColumn: col.goldColumnName,
      rowGroups: product.rowGroups, transforms: product.transforms,
      sources: [], columns: [col] };
    const gates = stepGates(state);
    expect(gates.slice(0, 3)).toEqual([true, true, true]);
    expect(unfinishedBefore(gates, 4)).toBe(-1);
  });
});

describe("the generated schema satisfies the registry contract", () => {
  // validate_gold_schemas.py check_master fails a master that lacks any of
  // these — a generated file that omits them is rejected by the repo's own
  // gate, which is where the previous version of this generator stopped.
  const REQUIRED_META = ["owner", "domain", "dataClassification",
                         "audience", "lifecycle"];

  const master = () => JSON.parse(newTableArtifacts({
    ...emptyWizard(), domain: "orders", destinationTable: "gold_orders.order",
    isNewTable: true, requestedBy: "someone@pearson.com",
    entityKey: [{ goldColumn: "orderId", drivingColumn: "ORDER_ID" }],
    columns: [{ goldColumnName: "orderId", businessDescription: "Order id",
      kind: "scalar" as const, dataType: "integer" as const, nullable: false,
      transform: null, source: { table: "T", column: "ORDER_ID", filter: null },
      channels: { event: true, message: true, api: true }, notes: null } as never],
  }).masterJson);

  it("carries every required metadata key, with audience fixed", () => {
    const m = master().metadata;
    for (const k of REQUIRED_META) expect(m, k).toHaveProperty(k);
    expect(m.audience).toBe("engineering");   // asserted exactly by the gate
    expect(m.owner.service).toBeTruthy();     // owner.service is checked too
    expect(m.owner.contact).toBe("someone@pearson.com");
    expect(m.lifecycle).toBe("draft");
  });

  it("declares the key as pairs and requires them, like program and price", () => {
    const m = master();
    expect(m.metadata.primaryKey)
      .toEqual([{ goldColumn: "orderId", drivingColumn: "ORDER_ID" }]);
    expect(m.required).toEqual(["orderId"]);
    expect(m.$schema).toBe("http://json-schema.org/draft-07/schema#");
    expect(m.additionalProperties).toBe(false);
  });

  it("omits the contact rather than emitting a blank one", () => {
    const a = newTableArtifacts({
      ...emptyWizard(), domain: "orders", destinationTable: "gold_orders.order",
      isNewTable: true, requestedBy: "  ",
      entityKey: [{ goldColumn: "orderId", drivingColumn: "ORDER_ID" }],
      columns: [],
    });
    expect(JSON.parse(a.masterJson).metadata.owner).not.toHaveProperty("contact");
  });
});

// ── round-2 verification findings, each pinned ─────────────────────────────

describe("Sources counts equal the list they open", () => {
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;
  const summaries = tableSummaries({
    drivingTable: product.drivingTable, entityKey: product.entityKey,
    columns: product.columns });

  it("counts distinct COLUMNS, not source uses", () => {
    // the button said "95 attributes" for EGO and opened 46 rows — and 95 is
    // more attributes than the whole mapping has
    for (const t of summaries) {
      expect(t.usedBy).toBe(t.columns.length);
      expect(t.usedBy).toBeLessThanOrEqual(product.columns.length);
      expect(new Set(t.columns).size).toBe(t.columns.length);   // distinct
    }
    const ext = summaries.find((t) =>
      t.shortName.toUpperCase() === "EGO_MTL_SY_ITEMS_EXT_B")!;
    expect(ext.usedBy).toBeGreaterThan(20);
    // every listed name is a real column of the entity
    const names = new Set(product.columns.map((c) => c.goldColumnName));
    for (const n of ext.columns) expect(names.has(n)).toBe(true);
  });

  it("attributes each column to the tables it actually reads", () => {
    const bom = summaries.find((t) =>
      t.shortName.toUpperCase() === "BOM_COMPONENTS_B")!;
    expect([...bom.columns].sort())
      .toEqual(["customComponents", "standardComponents"]);
  });
});

describe("two pipelines in one domain (UIFPE-252)", () => {
  // the adversarial round's HIGH finding: pipelineFor was domain-keyed and
  // glob order made every product-domain task seed marketing_copy's 07:45 —
  // one routine modify away from silently rescheduling product's live 06:30
  // run. Schedules are per gold_id (= the entity), pinned here.
  it("resolves each entity to its OWN schedule, never the domain's", () => {
    expect(pipelineFor("product")?.startTime).toBe("06:30");
    expect(pipelineFor("marketing_copy")?.startTime).toBe("07:45");
    expect(pipelineFor("marketing_copy")?.domain).toBe("product");
  });

  it("a modify deep-link seeds the entity schedule, not a sibling's", () => {
    const product = REGISTERED.find(
      (r) => r.table === "gold_product.product")!;
    const col = product.columns[0].goldColumnName;
    const r = resolveEntry(
      new URLSearchParams(`modify=${product.table}|${col}`), null, null);
    expect(r.state.startTime).toBe("06:30");
  });

  it("every registered pipeline's gold_id is unique", () => {
    const ids = PIPELINES.map((p) => p.goldId);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain("marketing_copy");
  });
});

describe("two databases, same table name", () => {
  // sameTable() has always been database-aware; the card model was not, so
  // one card swallowed both and the other lost its summary entirely.
    // mtl_parameters is the real case: it exists in BOTH
    // uif_echo_dev_silver_mdm and _tep, so these two rows are a live
    // ambiguity rather than a contrivance.
  const col = (name: string, table: string) => ({
    goldColumnName: name, businessDescription: name, kind: "scalar" as const,
    dataType: "string" as const, nullable: true, transform: null, notes: null,
    channels: { event: false, message: false, api: true },
    source: { table, column: "X", filter: null,
      join: { type: "left" as const, on: [
        { leftTable: "uif_echo_dev_silver_mdm.mtl_system_items_b",
          leftColumn: "INVENTORY_ITEM_ID", rightColumn: "INVENTORY_ITEM_ID" },
        { leftTable: "uif_echo_dev_silver_mdm.mtl_system_items_b",
          leftColumn: "ORGANIZATION_ID", rightColumn: "ORGANIZATION_ID" }] } },
  });

  it("keeps them apart, each with its own card", () => {
    const out = tableSummaries({
      drivingTable: "uif_echo_dev_silver_mdm.mtl_system_items_b",
      entityKey: [
        { goldColumn: "inventoryItemId", drivingColumn: "INVENTORY_ITEM_ID" },
        { goldColumn: "organizationId", drivingColumn: "ORGANIZATION_ID" }],
      columns: [
        col("fromMdm", "uif_echo_dev_silver_mdm.mtl_parameters") as never,
        col("fromTep", "uif_echo_dev_silver_tep.mtl_parameters") as never],
    });
    const tls = out.filter((t) =>
      t.shortName.toLowerCase() === "mtl_parameters");
    expect(tls.length).toBe(2);
    expect(tls.flatMap((t) => t.columns).sort())
      .toEqual(["fromMdm", "fromTep"]);
    for (const t of tls) expect(t.usedBy).toBe(1);
  });
});

describe("qualifyTable picks the entity own database", () => {
  it("prefers the domain silver database when the name is ambiguous", () => {
    // mtl_system_items_tl exists in several databases; without the domain
    // preference the wrong one was written into the task's sources. With no
    // preference at all the standard catalog wins, never one being retired.
    expect(qualifyTable("MTL_SYSTEM_ITEMS_TL", "product"))
      .toBe("uif_echo_dev_silver_mdm.mtl_system_items_tl");
    expect(qualifyTable("MTL_SYSTEM_ITEMS_TL")?.startsWith("uif_echo_dev")).toBe(true);
    expect(qualifyTable("NO_SUCH_TABLE_ANYWHERE", "product")).toBeUndefined();
  });
});

describe("sourceStanding is one definition for both screens", () => {
  it("separates a missing table from an un-wired one", () => {
    expect(sourceStanding("MTL_SYSTEM_ITEMS_B", "product")).toBe("ok");
    // in the catalog, not a registered runtime source for product (the BOM
    // tables played this part until UIFPE-269 registered them)
    expect(sourceStanding("BOM_COMPONENTS_B", "product")).toBe("ok");
    expect(sourceStanding("MTL_RELATED_ITEMS", "product")).toBe("not-wired");
    expect(sourceStanding("NO_SUCH_TABLE_ANYWHERE", "product")).toBe("unknown");
  });

  it("still flags a missing table for a domain with no manifest", () => {
    // sku has no manifest (price gained one with UIFPE-263): silence there
    // was read as "everything is wired", so the page warned about nothing
    expect(sourceStanding("NO_SUCH_TABLE_ANYWHERE", "sku")).toBe("unknown");
    expect(sourceStanding("MTL_SYSTEM_ITEMS_B", "sku")).toBe("ok");
  });
});

describe("entry resolution after the round-2 findings", () => {
  const P = (s = "") => new URLSearchParams(s);
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;
  const draftOf = (over: Record<string, unknown>) => ({
    state: { ...emptyWizard(), taskName: "MID_FLIGHT", domain: "product",
             destinationTable: product.table, intent: "modify" as const,
             columns: [structuredClone(product.columns[0])], ...over },
    step: 2, savedAt: "2026-08-27T10:00:00Z", v: 2,
  });

  it("keeps the task when the user switched which attribute they edit", () => {
    // entered via ?modify=A, switched to B inside the wizard, then refreshed:
    // comparing targetColumn called that a different task and reset to A
    const a = product.columns[0].goldColumnName;
    const b = product.columns[1].goldColumnName;
    const got = resolveEntry(P(`modify=${product.table}|${a}`), null,
                             draftOf({ targetColumn: b }));
    expect(got.state.taskName).toBe("MID_FLIGHT");
    expect(got.state.targetColumn).toBe(b);
  });

  it("does not restore across entities", () => {
    const other = REGISTERED.find((r) => r.domain === "program")!;
    const got = resolveEntry(
      P(`modify=${other.table}|${other.columns[0].goldColumnName}`), null,
      draftOf({}));
    expect(got.state.taskName).not.toBe("MID_FLIGHT");
    expect(got.state.destinationTable).toBe(other.table);
  });

  it("prefers the draft over the seed history keeps re-delivering", () => {
    // location.state lives in history: a reload re-delivers the import seed
    // forever, and writing it back was the data-loss defect reintroduced
    const seed = { ...emptyWizard(), taskName: "STALE_IMPORT",
                   domain: "product", destinationTable: product.table };
    const got = resolveEntry(P(), { seed, step: COLUMNS_STEP }, draftOf({}));
    expect(got.state.taskName).toBe("MID_FLIGHT");
  });

  it("uses the handoff when storage kept no draft at all", () => {
    const seed = { ...emptyWizard(), taskName: "IMPORTED", domain: "product" };
    expect(resolveEntry(P(), { seed, step: COLUMNS_STEP }, null)
      .state.taskName).toBe("IMPORTED");
  });
});

describe("import intent cannot contradict itself", () => {
  const file = (obj: unknown) =>
    ({ text: async () => JSON.stringify(obj) } as File);
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;

  it("refuses a modify envelope for a table with no registered mapping", () => {
    // intent modify + isNewTable true both held: Review claimed to CREATE the
    // table and to recalculate historical rows at the same time
    return expect(seedFromImport(file({
      task: "T", intent: "modify",
      intake: { domain: "orders", table: "gold_orders.order",
                columns: [{ ...product.columns[0] }] },
    }))).rejects.toThrow(/no mapping is registered/);
  });

  it("refuses a modify envelope carrying attributes that do not exist", () => {
    // nothing flagged these (registeredNames is empty for a modify) and the
    // compiler raises KeyError on the stranger
    return expect(seedFromImport(file({
      task: "T", intent: "modify",
      intake: { domain: "product", table: product.table,
                columns: [{ ...product.columns[0],
                            goldColumnName: "notAThing" }] },
    }))).rejects.toThrow(/not registered on it/);
  });

  it("never returns modify together with isNewTable", async () => {
    const s = await seedFromImport(file({
      domain: "product", table: product.table,
      drivingTable: product.drivingTable, columns: product.columns }));
    expect(s.intent === "modify" && s.isNewTable).toBe(false);
  });
});

// ── UIFPE-260: new-table scaffold plumbing ───────────────────────────────────
import { envTemplateSilver } from "../silverCatalog";

describe("new-table scaffold plumbing (UIFPE-260)", () => {
  it("env-templates a qualified silver table and refuses odd shapes", () => {
    expect(envTemplateSilver(
      "uif_echo_dev_silver_tep.xxpso_inv_stock_atp_stg"))
      .toBe("uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg");
    expect(envTemplateSilver("uif_echo_uat_silver_mdm.items"))
      .toBe("uif_echo_{env}_silver_mdm.items");
    expect(envTemplateSilver("no_env_marker.table")).toBeNull();
    expect(envTemplateSilver("bare_table")).toBeNull();
  });

  it("emptyWizard carries an empty driving filter", () => {
    expect(emptyWizard().drivingFilter).toBe("");
  });
});

describe("import re-scaffolds an UNREGISTERED entity as a new-table task (UIFPE-260)", () => {
  const file = (obj: unknown) =>
    ({ text: async () => JSON.stringify(obj) } as File);
  const product = REGISTERED.find((r) => r.table === "gold_product.product")!;

  it("accepts a full-column intake for an unregistered table and seeds the new-table journey", async () => {
    // The target is deliberately an entity that is NOT registered -- that is
    // what makes this the new-table path (entryMethods' `!reg` branch). It used
    // to import onto gold_stock.stock as a "draft", which stopped being true
    // when stock went live: isNewTable turned false and this test had been
    // failing on develop unnoticed, because no CI ran this suite (UIFPE-738).
    // Keying a mechanics test to whichever real entity is a draft this month is
    // the mistake; the draft branch itself is covered separately below.
    const s = await seedFromImport(file({
      domain: "stock", table: "gold_stock.stock_atp",
      drivingTable: "XXPSO_INV_STOCK_ATP_STG",
      drivingSilverTable:
        "uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg",
      drivingFilter: "PPID IS NOT NULL",
      entityKey: [
        { goldColumn: "ppid", drivingColumn: "PPID" },
        { goldColumn: "organizationCode",
          drivingColumn: "ORGANIZATION_CODE" },
      ],
      columns: [
        { goldColumnName: "ppid", businessDescription: "k", kind: "scalar",
          dataType: "string",
          source: { table: "XXPSO_INV_STOCK_ATP_STG", column: "PPID",
                    filter: null },
          transform: null, nullable: false,
          channels: { event: true, message: true, api: true } },
        { goldColumnName: "atpFlag", businessDescription: "new",
          kind: "scalar", dataType: "string",
          source: { table: "XXPSO_INV_STOCK_ATP_STG", column: "ATP_FLAG",
                    filter: null },
          transform: null, nullable: true,
          channels: { event: false, message: false, api: true } },
      ],
    }));
    expect(s.isNewTable).toBe(true);
    expect(s.intent).toBe("add");
    expect(s.drivingFilter).toBe("PPID IS NOT NULL");
    // identity step needs the QUALIFIED table, resolved via the catalog
    expect(s.drivingTable)
      .toBe("uif_echo_dev_silver_tep.xxpso_inv_stock_atp_stg");
    expect(s.targetColumn).toBeNull();
    // a bare intake has no source graph — the driving table seeds Sources
    // so the gate chain has no dead end
    expect(s.sources.map((x) => x.table))
      .toEqual(["uif_echo_dev_silver_tep.xxpso_inv_stock_atp_stg"]);
  });

  // The OTHER half of the new-table rule: a REGISTERED entity whose master is
  // still a draft. No real entity can exercise it -- all five registered
  // masters are `active` (2026-09-30) -- and that is not hypothetical: with
  // this test absent, deleting `lifecycle === "draft"` from entryMethods left
  // all 309 transform-ui tests green. Verified, then covered here rather than
  // dropped, because the next new entity registers as a draft and re-scaffolds
  // through exactly this branch. The lifecycle is stubbed for this test only.
  it("re-scaffolds a REGISTERED entity while its master is still a draft", async () => {
    vi.resetModules();
    vi.doMock("../masterSchema", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../masterSchema")>();
      return {
        ...actual,
        findMaster: (d: string, e: string) => {
          const m = actual.findMaster(d, e);
          return m && { ...m, metadata: { ...m.metadata, lifecycle: "draft" } };
        },
      };
    });
    try {
      const { seedFromImport: seedDraft } = await import("../entryMethods");
      const s = await seedDraft(file({
        // REGISTERED, so `!reg` is false and only the draft lifecycle can make
        // this a new-table task -- which is what pins the branch.
        domain: "stock", table: "gold_stock.stock",
        drivingTable: "XXPSO_INV_STOCK_ATP_STG",
        drivingSilverTable:
          "uif_echo_{env}_silver_tep.xxpso_inv_stock_atp_stg",
        entityKey: [{ goldColumn: "productId", drivingColumn: "PPID" }],
        columns: [
          { goldColumnName: "productId", businessDescription: "k",
            kind: "scalar", dataType: "string", nullable: false,
            transform: null,
            source: { table: "XXPSO_INV_STOCK_ATP_STG", column: "PPID",
                      filter: null },
            channels: { event: true, message: true, api: true } },
        ],
      }));
      expect(s.isNewTable).toBe(true);
      expect(s.intent).toBe("add");
    } finally {
      vi.doUnmock("../masterSchema");
      vi.resetModules();
    }
  });

  it("still refuses the mix for a LIVE table (no drivingSilverTable)", async () => {
    await expect(seedFromImport(file({
      domain: "product", table: product.table,
      drivingTable: product.drivingTable,
      columns: [product.columns[0],
                { ...product.columns[0], goldColumnName: "brandNewThing" }],
    }))).rejects.toThrow(/mixes new and existing/);
  });
});

describe("price re-scaffold import (UIFPE-262)", () => {
  const file = (obj: unknown) =>
    ({ text: async () => JSON.stringify(obj) } as File);

  it("seeds lookup tables into Sources and carries pick + silverTables", async () => {
    // Unregistered target, for the same reason as the stock case above: this
    // test is about a nested column's JOIN table becoming a Source and `pick`
    // surviving the import, neither of which depends on the entity. Aimed at
    // gold_price.price it broke twice over once price went live -- the entity
    // stopped being a draft, and the mapping renamed ppid to productId
    // (2026-09-23), so the intake read as a mix of new and existing columns
    // and seedFromImport refused it outright (UIFPE-738).
    const raw = JSON.parse(JSON.stringify({
      domain: "price", table: "gold_price.price_list",
      drivingTable: "XXPSO_QP_VAT_PRICE_STG",
      drivingSilverTable:
        "uif_echo_{env}_silver_tep.xxpso_qp_vat_price_stg",
      entityKey: [{ goldColumn: "ppid", drivingColumn: "PPID" }],
      columns: [
        { goldColumnName: "ppid", businessDescription: "k", kind: "scalar",
          dataType: "string", nullable: false, transform: null,
          source: { table: "XXPSO_QP_VAT_PRICE_STG", column: "PPID",
                    filter: null },
          channels: { event: true, message: true, api: true } },
        { goldColumnName: "priceSpecifications", businessDescription: "n",
          kind: "nested", joinKey: "PPID",
          channels: { event: true, message: true, api: true },
          pick: { partitionBy: ["currency"],
                  orderBy: [{ expr: "END_DATE", direction: "desc" }] },
          fields: [
            { name: "currency", type: "string", sortKey: true,
              source: { table: "QP_LIST_HEADERS_B", column: "CURRENCY_CODE",
                        join: { type: "left",
                                on: [{ leftColumn: "LIST_HEADER_ID",
                                       rightColumn: "LIST_HEADER_ID" }] } } },
            { name: "netPrice", type: "number", sortKey: false,
              source: { table: "XXPSO_QP_VAT_PRICE_STG",
                        column: "NET_PRICE" } },
          ] },
      ],
    }));
    const s = await seedFromImport(file(raw));
    expect(s.isNewTable).toBe(true);
    expect(s.sources.map((x) => x.table)).toEqual([
      "uif_echo_dev_silver_tep.xxpso_qp_vat_price_stg",
      "uif_echo_dev_silver_tep.qp_list_headers_b",
    ]);
    const nested = s.columns.find((c) => c.kind === "nested") as any;
    expect(nested.pick.partitionBy).toEqual(["currency"]);
  });
});
