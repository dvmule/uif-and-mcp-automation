// Mirrors stacks/apicurio/standards/gold-attribute-mapping.schema.json — the intake
// contract every wizard route compiles into. Keep field names identical to the
// schema; the compiler consumes this shape verbatim. The wizard renders THIS
// contract (scalar / conditional / nested), never a domain's topology — that
// is what makes it work for every domain.

// TYPE-ONLY import on purpose (UIFPE-149): the cadence ladder lives in
// pipelines.ts, which runs an import.meta.glob over every domain's pipeline
// YAML. `import type` is erased at compile time, so types.ts — imported by
// almost every module here — does not drag that glob along with it.
import type { GoldFrequency, Weekday } from "./pipelines";

export type DataType =
  | "string" | "integer" | "number" | "float" | "boolean" | "date" | "timestamp";

export interface SourceJoin {
  type?: "left" | "inner";
  on: { leftTable: string; leftColumn: string; rightColumn: string }[];
}

export interface SourceRef {
  table: string;
  column: string;
  filter: string | null;
  // Structured attachment when this table is not the driving table — the
  // compiler renders it; the UI displays it. Never prose.
  join?: SourceJoin | null;
}

export interface Channels {
  event: boolean;
  message: boolean;
  api: boolean;
}

// ── Gold table identity (UIFPE-107) ─────────────────────────────────────────
// The gold table's PRIMARY KEY, ordered: each entry maps a gold key column to
// the driving-table column it comes from. This is the grain every attribute
// attaches to — join editors prefill from it, previews render it instead of
// {entity_key} placeholders, and a new entity's scaffold reads it for the
// runtime key_mapping.

export interface EntityKeyPair {
  goldColumn: string;      // camelCase gold column (e.g. inventoryItemId)
  drivingColumn: string;   // driving-table column (e.g. INVENTORY_ITEM_ID)
}

// The 90% attachment: a lookup joins the driving table on the entity key,
// same column names both sides (true for every PDH extension table). One
// click instead of hand-typing the pairs; the user edits from there.
export function entityKeyJoin(entityKey: EntityKeyPair[],
                              drivingTable: string): SourceJoin | null {
  if (!entityKey.length || !drivingTable) return null;
  return { type: "left", on: entityKey.map((p) => ({
    leftTable: drivingTable, leftColumn: p.drivingColumn,
    rightColumn: p.drivingColumn })) };
}

/** How this task ALREADY attached a table (UIFPE-219): the latest join any
 *  column declared over the same table, so the second column reading it
 *  inherits the first's (possibly hand-tuned) attachment instead of the raw
 *  entity-key default. The user touches a join only when a column genuinely
 *  joins differently — the reason joins are column-level at all. */
export function rememberedJoin(columns: MappingColumn[], table: string):
  { join: SourceJoin; fromColumn: string } | null {
  if (!table) return null;
  let found: { join: SourceJoin; fromColumn: string } | null = null;
  const consider = (src: SourceRef | undefined, colName: string) => {
    if (src?.join?.on?.length && src.table && sameTable(src.table, table))
      found = { join: structuredClone(src.join), fromColumn: colName };
  };
  for (const c of columns) {
    if (c.kind === "scalar") consider(c.source, c.goldColumnName);
    else if (c.kind === "conditional")
      for (const b of c.branches ?? []) consider(b.source, c.goldColumnName);
  }
  return found;   // last found = the task's latest word on this table
}

// New tables must declare a complete key before anything can attach to it.
// With the driving table's catalog columns supplied, membership is enforced
// too: every key column comes from the PRIMARY table by contract (the
// runtime's key_mapping maps each source's rows to gold PK values — lookups
// contribute attributes, never key columns). A grain no single Silver table
// carries is a grain-defining JOIN — an architecture case, not a wizard input.
export function entityKeyProblems(pairs: EntityKeyPair[],
                                  drivingCols?: string[]): string[] {
  const out: string[] = [];
  if (!pairs.length) out.push("declare at least one entity-key column");
  pairs.forEach((p, i) => {
    if (!p.goldColumn || !/^[a-z][a-zA-Z0-9]*$/.test(p.goldColumn))
      out.push(`key ${i + 1}: gold column must be camelCase`);
    if (!p.drivingColumn)
      out.push(`key ${i + 1}: pick the driving-table column it comes from`);
    else if (drivingCols?.length && !drivingCols.includes(p.drivingColumn))
      out.push(`${p.drivingColumn} is not a column of the primary table — the `
        + "entity key must come from it (lookups contribute attributes, never "
        + "key columns); a grain spanning two tables needs a grain-defining "
        + "join — raise it with the framework owners");
  });
  const dup = pairs.map((p) => p.goldColumn).filter((v, i, a) => v && a.indexOf(v) !== i);
  if (dup.length) out.push(`duplicate key column ${dup[0]}`);
  return out;
}

// ── Transform engine (UIFPE-105) ────────────────────────────────────────────
// A transform is STRUCTURED data compiled deterministically around the source
// expression {x} — never prose. Same single-source-of-truth pattern as
// rowGroups: named rules live once in the mapping's `transforms` registry and
// columns reference them ({rule: "pearsonImageUrl"}), so every attribute
// using a rule compiles literally the same SQL.

export interface TransformStep {
  op: "round" | "cast" | "trim" | "upper" | "lower" | "coalesceTo";
  precision?: number;   // round
  to?: string;          // cast target, e.g. DECIMAL(38,2)
  value?: string;       // coalesceTo literal
}

export interface TransformCase {
  when: { op: "startsWith" | "like" | "equals" | "in" | "isNull";
          values?: string[] };
  then: { op: "prepend" | "append" | "replaceText" | "literal" | "null" | "keep";
          text?: string; find?: string; replaceWith?: string };
}

export interface TransformDef {
  description?: string | null;
  steps?: TransformStep[];     // pipeline, applied in order
  cases?: TransformCase[];     // guarded CASE WHEN, evaluated in order
  else?: "keep" | "null";      // cases fallthrough; default keep
  sqlExpr?: string;            // gated escape hatch — SQL fragment with {x}
}

// A column's transform: null = straight copy; {rule} references the named
// registry; an inline def defines one in place. Prose strings are DEPRECATED
// (UIFPE-105) — tolerated on old drafts, flagged by validation.
export type TransformRef = null | string | { rule: string } | TransformDef;

// The definition a transform reference actually means. `missing` marks a
// reference to a rule the mapping doesn't define; `prose` carries deprecated
// free text that must be restructured before submission.
export interface ResolvedTransform {
  def?: TransformDef;
  rule?: string | null;    // set when the def came from the named registry
  prose?: string | null;
  missing?: boolean;
}

export function resolveTransform(t: TransformRef | undefined,
                                 transforms: Record<string, TransformDef>):
                                 ResolvedTransform | null {
  if (t == null) return null;
  if (typeof t === "string") return t.trim() ? { prose: t } : null;
  if ("rule" in t && typeof (t as any).rule === "string") {
    const def = transforms[(t as { rule: string }).rule];
    return def ? { def, rule: (t as { rule: string }).rule }
               : { rule: (t as { rule: string }).rule, missing: true };
  }
  return { def: t as TransformDef };
}

export interface ScalarColumn {
  goldColumnName: string;
  businessDescription: string;
  kind: "scalar";
  dataType: DataType;
  source: SourceRef;
  transform: TransformRef;
  channels: Channels;
  nullable: boolean;
  joinGrain?: string;   // DEPRECATED (UIFPE-104) — tolerated on old drafts only
  derivation?: DerivationBlock;
  notes: string | null;
}

// A NAMED row group — user-maintained rule set, defined once per mapping
// (rowGroups) and referenced by branch scopes. One place to define "UK orgs";
// every column fanning out by it uses literally the same predicate. A
// complement group ("nonUkOrgs") is defined AS the complement of its base, so
// the pair can never drift apart.
export type RowGroupDef =
  | { table?: string | null; column: string;
      op: "equals" | "in" | "startsWith"; values: string[];
      description?: string | null }
  | { complementOf: string; description?: string | null };

// Proliferate grain only: WHICH entity rows receive this branch's value —
// either a reference to a named rowGroups entry ({group: "ukOrgs"}) or an
// inline predicate. Structured so the compiler renders the fan-out CASE
// deterministically. Two branches may share one discriminator condition and
// differ only in scope (product: digital UK vs digital non-UK).
export interface BranchScope {
  group?: string | null;   // name of a rowGroups entry — wins over inline
  table?: string | null;   // entity-grain table the column lives on
  column?: string;
  op?: "equals" | "in" | "startsWith" | "else";
  values?: string[];       // empty for else
  label?: string | null;   // human name for an inline group
}

// The predicate a scope actually means, with references resolved. `missing`
// marks a reference to a group the mapping doesn't define.
export interface ResolvedScope {
  table?: string | null;
  column: string;
  op: "equals" | "in" | "startsWith" | "else";
  values: string[];
  label?: string | null;
  not?: boolean;     // complement group: NOT (predicate)
  missing?: boolean;
}

export function resolveScope(s: BranchScope,
                             rowGroups: Record<string, RowGroupDef>): ResolvedScope {
  if (s.group) {
    const g = rowGroups[s.group];
    if (!g) return { column: "", op: "equals", values: [], label: s.group, missing: true };
    if ("complementOf" in g) {
      const base = rowGroups[g.complementOf];
      if (!base || "complementOf" in base)
        return { column: "", op: "equals", values: [], label: s.group, missing: true };
      return { table: base.table, column: base.column, op: base.op,
               values: base.values, label: s.group, not: true };
    }
    return { table: g.table, column: g.column, op: g.op, values: g.values, label: s.group };
  }
  return { table: s.table, column: s.column ?? "", op: s.op ?? "equals",
           values: s.values ?? [], label: s.label ?? null };
}

// ── derived columns (ADR-0008, UIFPE-217): the query is the contract ───────
// A column whose value takes several steps to work out carries the author's
// actual SELECT plus a declared key/value contract. NOT a fourth kind: an
// optional block on a column of any kind — kind is the value's shape,
// derivation is how the value is reached. The compiler splices the query
// VERBATIM (CTE + LEFT join on the contract); `reads` is COMPUTED by the
// Python validator from the parsed query and re-checked in CI, never typed.
export interface DerivationKeyPair {
  /** Output column of the query that identifies the entity. */
  queryColumn: string;
  /** Driving-table column the compiler joins it against. */
  entityColumn: string;
}

export interface DerivationBlock {
  sql: string;
  keyedBy: DerivationKeyPair[];
  /** Defaults to the goldColumnName. */
  valueColumn?: string;
  reads?: Record<string, string[]>;
}

export interface ConditionalBranch {
  condition: string;
  scope?: BranchScope | null;
  source: SourceRef;
  transform?: TransformRef;
  fallback: "returnNull" | "fallbackToElse";
  joinGrain?: string;   // DEPRECATED (UIFPE-104)
  notes: string | null;
}

export interface ConditionalColumn {
  goldColumnName: string;
  businessDescription: string;
  kind: "conditional";
  dataType: DataType;
  discriminator: { table: string; column: string };
  channels: Channels;
  branches: ConditionalBranch[];
  // Applied AFTER the branch pick — wraps the value the winning branch
  // produced (coverImage*: URL normalization over whichever catalog won).
  // A branch's own transform applies before the pick, to that branch only.
  transform?: TransformRef;
  derivation?: DerivationBlock;
  notes: string | null;
}

export interface NestedField {
  name: string;
  type: string;
  sortKey: boolean;
  // Full sourceRef: fields of one array may come from DIFFERENT tables
  // (driving array table + lookups joined at the array's grain).
  // null = declared with no Silver source yet (rendered NULL until one registers).
  source: { table: string; column: string; filter?: string | null } | null;
  transform?: TransformRef;
}

export interface NestedColumn {
  goldColumnName: string;
  businessDescription: string;
  kind: "nested";
  joinKey: string;
  filter?: string | null;
  channels: Channels;
  joinGrain?: string;   // DEPRECATED (UIFPE-104)
  fields: NestedField[];
  /** Declarative ranked pick (UIFPE-262, new-entity scaffolds): keep ONE
   *  source row per partition before the array assembles — how "the ACTIVE
   *  price, else the latest end-dated" is said without hand-written SQL. */
  pick?: {
    partitionBy: string[];
    orderBy: { expr: string; direction?: "asc" | "desc" }[];
  };
  derivation?: DerivationBlock;
  notes: string | null;
}

export type MappingColumn = ScalarColumn | ConditionalColumn | NestedColumn;

export interface IntakeMapping {
  $schema: string;
  domain: string;
  table: string;
  drivingTable?: string | null;
  entityKey?: EntityKeyPair[];
  rowGroups?: Record<string, RowGroupDef>;
  transforms?: Record<string, TransformDef>;
  batch: {
    requestedBy: string;
    targetDate: string | null;
    backfillMode: "silent" | "emit";
    newChannel: string | null;
  };
  columns: MappingColumn[];
}

// ── Wizard task state ───────────────────────────────────────────────────────

export type EntryMethod = "guided" | "import" | "ai" | "sql";
export type TaskIntent = "add" | "modify";

// One ON-pair of a join: <already-declared table>.<column> = <this table>.<column>.
// Chains compose by construction: C can join B which joins A, because the
// left side must reference a table declared EARLIER in the list.
export interface JoinCond {
  leftTable: string;
  leftColumn: string;
  rightColumn: string;
}

export interface SourceDecl {
  table: string;      // e.g. uif_echo_{env}_silver_mdm.mtl_system_items_b
  eav: boolean;       // hint: rows are (key, attribute-group) shaped — column
                      // forms will remind you to set a source.filter
  joinType: "left" | "inner";   // how this table attaches (driving table: ignored)
  joins: JoinCond[];            // required for every table after the first
}

export interface WizardState {
  taskName: string;
  method: EntryMethod | null;
  intent: TaskIntent | null;
  domain: string | null;             // discovered from registered mappings, or new
  destinationTable: string | null;   // existing gold table or a new name
  drivingTable: string | null;       // the entity's spine table (registered
                                     // mapping's drivingTable; new tables
                                     // pick it explicitly at Destination)
  entityKey: EntityKeyPair[];        // the gold table's PK, mapped to
                                     // driving-table columns (UIFPE-107)
  frequency: GoldFrequency;          // how OFTEN this table refreshes — one of
                                     // the 11 rungs (UIFPE-149)
  startTime: string;                 // from WHEN, as "HH:MM" UTC. Per-pipeline,
                                     // so two daily tables can run at different
                                     // times; together with frequency it defines
                                     // the slots the dispatcher runs it at
  weeklyDays: Weekday[];             // WHICH days, UTC, on the weekly rung only
                                     // (UIFPE-532); carried on every rung so a
                                     // cadence switch keeps the user's pick
  rowGroups: Record<string, RowGroupDef>;  // named fan-out groups (registered
                                     // + any the task defines)
  transforms: Record<string, TransformDef>; // named transform rules (registered
                                     // + any the task defines)
  isNewTable: boolean;
  drivingFilter: string;             // new-table row domain (UIFPE-260): a SQL
                                     // predicate over driving-table columns;
                                     // becomes the scaffolded SQL's WHERE and
                                     // the manifest's sot_filter together
  targetColumn: string | null;       // modify flow: existing goldColumnName
  sources: SourceDecl[];             // tables this task reads (add flow)
  columns: MappingColumn[];          // the batch being authored — mirrors the
                                     // template's three sheets
  refreshHistorical: boolean;        // modify flow: run F3 --refresh_columns
  refreshMode: "emit" | "silent";
  requestedBy: string;               // intake batch metadata, captured at Review
  targetDate: string | null;
  ticket?: string;                   // UIFPE-<n> — the PR is rejected without
                                     // one, so Review collects it (UIFPE-218)
  authorEmail?: string;              // the submitter's exact Jira address —
                                     // authors the submission commits
}

export const emptyWizard = (): WizardState => ({
  taskName: `Transformation_Task_${new Date()
    .toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}`,
  method: null,
  intent: null,
  domain: null,
  destinationTable: null,
  drivingTable: null,
  entityKey: [],
  frequency: "gold-24h",
  startTime: "06:30",
  weeklyDays: ["MON"],
  rowGroups: {},
  transforms: {},
  isNewTable: false,
  drivingFilter: "",
  targetColumn: null,
  sources: [],
  columns: [],
  refreshHistorical: true,
  refreshMode: "emit",
  requestedBy: "",
  targetDate: null,
  ticket: "",
  authorEmail: "",
});

// ── Kind conversion (UIFPE-108) ─────────────────────────────────────────────
// Attributes EVOLVE — the product entity's own history is scalars becoming
// conditionals when Digital Catalog arrived. Switching kind is a guided
// CONVERSION, never a reset: everything transferable moves, and what cannot
// move is REPORTED so the modal can show it (its Cancel is the safety net).

export interface KindConversion {
  column: MappingColumn;
  losses: string[];   // human-readable: what this conversion drops
}

const ELSE_INDEX = (branches: ConditionalBranch[]) => {
  const i = branches.findIndex((b) => (b.condition ?? "").trim().toUpperCase() === "ELSE");
  return i >= 0 ? i : 0;
};

export function convertColumnKind(col: MappingColumn,
                                  to: MappingColumn["kind"],
                                  surviveBranch = -1): KindConversion {
  if (col.kind === to) return { column: col, losses: [] };
  // derivation survives every conversion verbatim: kind is the value's
  // SHAPE, derivation is how the value is REACHED — they are orthogonal,
  // and a hand-written query is exactly the work a kind switch must not eat
  const base = { goldColumnName: col.goldColumnName,
                 businessDescription: col.businessDescription,
                 channels: col.channels, notes: col.notes ?? null,
                 ...(col.derivation ? { derivation: col.derivation } : {}) };
  const losses: string[] = [];

  // Everything converts through the SCALAR shape — the common denominator.
  let scalar: ScalarColumn;
  if (col.kind === "scalar") {
    scalar = col;
  } else if (col.kind === "conditional") {
    const keep = surviveBranch >= 0 && surviveBranch < col.branches.length
      ? surviveBranch : ELSE_INDEX(col.branches);
    const b = col.branches[keep];
    const dropped = col.branches.filter((_, i) => i !== keep);
    if (dropped.length)
      losses.push(`branches dropped: ${dropped.map((x) => x.condition || "(blank)").join(", ")} — keeping "${b?.condition ?? ""}"`);
    losses.push(`discriminator ${col.discriminator.table}.${col.discriminator.column} no longer applies`);
    if (dropped.some((x) => x.scope) || b?.scope)
      losses.push("branch fan-out scopes dropped");
    let transform = b?.transform ?? null;
    if (col.transform != null && b?.transform != null)
      losses.push("branch transform dropped — keeping the after-pick transform");
    if (col.transform != null) transform = col.transform;
    scalar = { ...base, kind: "scalar", dataType: col.dataType,
               source: b?.source ?? { table: "", column: "", filter: null },
               transform, nullable: true };
  } else {
    const f = col.fields.find((x) => x.sortKey) ?? col.fields[0];
    const others = col.fields.filter((x) => x !== f);
    if (others.length)
      losses.push(`array fields dropped: ${others.map((x) => x.name || "(unnamed)").join(", ")} — keeping "${f?.name ?? ""}"`);
    if (col.joinKey) losses.push(`array join key "${col.joinKey}" no longer applies`);
    scalar = { ...base, kind: "scalar",
               dataType: (f?.type as DataType) ?? "string",
               source: { table: f?.source?.table ?? "",
                         column: f?.source?.column ?? "",
                         filter: f?.source?.filter ?? col.filter ?? null },
               transform: f?.transform ?? null, nullable: true };
  }

  if (to === "scalar") return { column: scalar, losses };
  if (to === "conditional") {
    // Lossless from scalar: the source becomes the ELSE branch, and a
    // column-level transform is ALREADY the after-pick slot.
    return { losses, column: { ...base, kind: "conditional",
      dataType: scalar.dataType,
      discriminator: { table: "", column: "" },
      branches: [{ condition: "ELSE", source: scalar.source,
                   transform: null, fallback: "returnNull", notes: null }],
      transform: scalar.transform ?? null } };
  }
  return { losses, column: { ...base, kind: "nested",
    joinKey: "", filter: null,
    fields: [{ name: "value", type: scalar.dataType, sortKey: true,
               source: { table: scalar.source.table,
                         column: scalar.source.column,
                         filter: scalar.source.filter ?? null },
               transform: scalar.transform ?? null }] } };
}

// ── Entry-boundary normalization + task-level validation ───────────────────

// Columns arriving from OUTSIDE the wizard's own forms (imported files, AI
// drafts) may be missing optional-ish fields the forms always create — a
// column without `channels` used to crash the whole Columns step. Normalize at
// the boundary so render code never meets undefined.
// Transforms from outside sources arrive in any shape — keep only the ones
// the contract knows (null, prose string, {rule}, or a def carrying at least
// one of steps/cases/sqlExpr); anything else becomes null instead of
// crashing the renderer.
function normTransform(t: any): TransformRef {
  if (t == null) return null;
  if (typeof t === "string") return t.trim() || null;
  if (typeof t !== "object") return null;
  if (typeof t.rule === "string" && t.rule) return { rule: t.rule };
  if (Array.isArray(t.steps) || Array.isArray(t.cases)
      || typeof t.sqlExpr === "string")
    return t as TransformDef;
  return null;
}

export function normalizeColumns(cols: unknown[]): MappingColumn[] {
  const chan = (c: any) => ({ event: !!c?.event, message: !!c?.message, api: c?.api !== false });
  return (cols as any[])
    .filter((c) => c && typeof c.goldColumnName === "string"
      && ["scalar", "conditional", "nested"].includes(c.kind))
    .map((c) => {
      const base = { businessDescription: c.businessDescription ?? "",
                     channels: chan(c.channels), notes: c.notes ?? null };
      if (c.kind === "scalar")
        return { ...c, ...base, dataType: c.dataType ?? "string",
          source: { table: "", column: "", filter: null, ...(c.source ?? {}) },
          transform: normTransform(c.transform), nullable: c.nullable !== false };
      if (c.kind === "conditional")
        return { ...c, ...base, dataType: c.dataType ?? "string",
          discriminator: c.discriminator ?? { table: "", column: "" },
          transform: normTransform(c.transform),
          branches: (c.branches ?? []).map((b: any) => ({
            condition: b?.condition ?? "", scope: b?.scope ?? null,
            source: { table: "", column: "", filter: null, ...(b?.source ?? {}) },
            transform: normTransform(b?.transform),
            fallback: b?.fallback ?? "returnNull", notes: b?.notes ?? null })) };
      return { ...c, ...base, joinKey: c.joinKey ?? "", filter: c.filter ?? null,
        fields: (c.fields ?? []).map((f: any) => ({
          name: f?.name ?? "", type: f?.type ?? "string", sortKey: !!f?.sortKey,
          source: { table: "", column: "", ...(f?.source ?? {}) },
          transform: normTransform(f?.transform) })) };
    });
}

// Problems no single column can see: name collisions inside the task, and —
// for the add flow — collisions with the destination's registered columns
// (merging those would corrupt the mapping).
export function taskProblems(columns: MappingColumn[],
                             registeredNames: string[] = []): string[] {
  const out: string[] = [];
  const seen = new Map<string, number>();
  for (const c of columns)
    if (c.goldColumnName)
      seen.set(c.goldColumnName, (seen.get(c.goldColumnName) ?? 0) + 1);
  for (const [name, n] of seen)
    if (n > 1) out.push(`"${name}" is defined ${n} times in this task`);
  const reg = new Set(registeredNames);
  for (const name of seen.keys())
    if (reg.has(name))
      out.push(`"${name}" already exists on the destination table — use the modify flow to change it`);
  return out;
}

// ── Validation mirroring the intake schema's invariants ────────────────────

// The first declared source is the PRIMARY table (the entity spine). For an
// EXISTING entity it is fixed by the registered mapping — declaring a
// different first source would silently change what a row of the table means.
// The Sources step registers TABLES ONLY (UIFPE-219): how each column
// attaches is column-level logic, captured and validated in the column
// editor (columnProblems checks every attachment's ON pairs), so no join
// completeness is demanded here. A task-level join a draft may still carry
// (the pre-219 shape, or an imported envelope) is tolerated as pre-fill
// data, never required.
export function sourceProblems(sources: SourceDecl[],
                               drivingTable?: string | null): string[] {
  const out: string[] = [];
  if (drivingTable && sources.length && !sameTable(sources[0].table, drivingTable))
    out.push(`first source must be this entity's primary table (${shortName(drivingTable)}) — it decides which rows exist`);
  return out;
}

// A source's attachment (sourceRef.join) must be complete to compile — a
// half-edited ON pair would render broken SQL.
function joinProblems(src: SourceRef | undefined, label: string): string[] {
  return (src?.join?.on ?? []).flatMap((j, k) =>
    !j.leftTable || !j.leftColumn || !j.rightColumn
      ? [`${label}: attachment ON ${k + 1} is incomplete`] : []);
}

// Task-level context for validation that a column can't judge alone —
// which table is the entity's spine, and what the Sources step declared.
export interface ColumnContext {
  sources: SourceDecl[];
  drivingTable: string | null;
  rowGroups?: Record<string, RowGroupDef>;
  transforms?: Record<string, TransformDef>;
}

// A transform must be complete to compile: named rules must exist, every
// step/case must carry its operands, custom SQL must reference {x}, and
// prose (the pre-UIFPE-105 shape) must be restructured before submission.
export function transformProblems(t: TransformRef | undefined,
                                  transforms: Record<string, TransformDef>,
                                  label: string): string[] {
  const r = resolveTransform(t, transforms);
  if (!r) return [];
  if (r.prose)
    return [`${label}: transform is free text — pick a named rule or define it structurally`];
  if (r.missing)
    return [`${label}: transform rule “${r.rule}” is not defined in this mapping`];
  const d = r.def!;
  const at = r.rule ? `${label}: rule “${r.rule}”` : label;
  const out: string[] = [];
  if (!d.steps?.length && !d.cases?.length && !d.sqlExpr)
    out.push(`${at}: transform has no steps, cases, or SQL`);
  d.steps?.forEach((s, i) => {
    if (s.op === "round" && s.precision == null)
      out.push(`${at}: step ${i + 1} (round) needs a precision`);
    if (s.op === "cast" && !s.to)
      out.push(`${at}: step ${i + 1} (cast) needs a target type`);
    if (s.op === "coalesceTo" && s.value == null)
      out.push(`${at}: step ${i + 1} (coalesceTo) needs a value`);
  });
  d.cases?.forEach((c, i) => {
    if (c.when.op !== "isNull" && !c.when.values?.length)
      out.push(`${at}: case ${i + 1} needs at least one match value`);
    if (["prepend", "append", "literal"].includes(c.then.op) && !c.then.text)
      out.push(`${at}: case ${i + 1} (${c.then.op}) needs text`);
    if (c.then.op === "replaceText" && !c.then.find)
      out.push(`${at}: case ${i + 1} (replaceText) needs the text to find`);
  });
  if (d.sqlExpr && !d.sqlExpr.includes("{x}"))
    out.push(`${at}: custom SQL must reference the source value as {x}`);
  return out;
}

const shortName = (t: string) => t.split(".").pop() ?? t;

// Table identity across naming styles: the intake records bare PDH names
// (MTL_SYSTEM_ITEMS_B) while the wizard declares qualified Silver names
// (uif_echo_dev_silver_mdm.mtl_system_items_b). A bare name matches any
// database's table of that name — but two QUALIFIED names only match when
// their databases match too (the same table name in two databases is two
// different tables). `{env}` tokens match any environment segment.
const dbName = (t: string): string | null =>
  t.includes(".") ? t.slice(0, t.lastIndexOf(".")).toLowerCase() : null;
const sameDb = (a: string, b: string): boolean => {
  const pa = a.split("_"), pb = b.split("_");
  return pa.length === pb.length && pa.every((seg, i) =>
    seg === "{env}" || pb[i] === "{env}" || seg === pb[i]);
};
export const sameTable = (a?: string | null, b?: string | null): boolean => {
  if (!a || !b) return false;
  if (shortName(a).toLowerCase() !== shortName(b).toLowerCase()) return false;
  const da = dbName(a), db = dbName(b);
  return !da || !db || sameDb(da, db);
};

export function columnProblems(col: MappingColumn, ctx?: ColumnContext): string[] {
  const out: string[] = [];
  if (!col.goldColumnName) out.push("gold column name is required");
  else if (!/^[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)?$/.test(col.goldColumnName))
    out.push("name must be camelCase (dot-notation allowed for nested-object fields)");
  if (!col.businessDescription) out.push("business description is required");
  // A derivation block gates the COLUMN, not just its panel (UIFPE-219): a
  // half-filled query must never let the grid say "valid" while the submit
  // battery is certain to refuse it. Only the deterministic subset lives
  // here — the fuller advisory checks (sources, stability) are the panel's.
  if (col.derivation) {
    const d = col.derivation as Partial<DerivationBlock>;
    if (typeof d.sql !== "string" || !Array.isArray(d.keyedBy))
      out.push("the derivation was saved in an older format — re-import the "
        + "registered mapping or start a fresh query");
    else {
      if (col.goldColumnName.includes("."))
        out.push("a struct member cannot carry its own query — derive the "
          + "parent column");
      if (!d.sql.trim()) out.push("the derived query is empty");
      if (!d.keyedBy.length
          || d.keyedBy.some((k) => !String(k?.queryColumn ?? "").trim()
            || !String(k?.entityColumn ?? "").trim()))
        out.push("the derived query needs its key contract — which output "
          + "column identifies the entity");
    }
  }
  const tfms = ctx?.transforms ?? {};
  if (col.kind === "scalar") {
    if (!col.source?.table || !col.source?.column)
      out.push("source table and column are required");
    out.push(...joinProblems(col.source, "source"));
    out.push(...transformProblems(col.transform, tfms, "transform"));
    // A source that is NOT the entity's driving table must say how it
    // attaches — via sourceRef.join, the column's own declaration. (EAV
    // sources are exempt: they attach via the entity key, same predicate
    // the SQL preview uses.)
    if (ctx && col.source?.table && !col.source.join?.on?.length) {
      const driving = ctx.drivingTable ?? ctx.sources[0]?.table ?? null;
      const declared = ctx.sources.some((s) => sameTable(s.table, col.source.table));
      // UIFPE-104: EVERY non-driving source declares its join — EAV included
      // (the entity-key join is stated explicitly, not implied by a grain).
      if (driving && !declared && !sameTable(col.source.table, driving))
        out.push(`${shortName(col.source.table)} is not the driving table — define how it attaches (join type + ON fields)`);
    }
  }
  if (col.kind === "conditional") {
    if (!col.discriminator?.table || !col.discriminator?.column)
      out.push("discriminator table.column is required");
    if (!col.branches?.length) out.push("at least one branch is required");
    out.push(...transformProblems(col.transform, tfms, "after-pick transform"));
    col.branches?.forEach((b, i) => {
      if (!b.condition) out.push(`branch ${i + 1}: condition is required`);
      if (!b.source?.table || !b.source?.column)
        out.push(`branch ${i + 1}: source table and column are required`);
      out.push(...joinProblems(b.source, `branch ${i + 1}`));
      out.push(...transformProblems(b.transform, tfms, `branch ${i + 1}`));
      if (ctx && b.source?.table && !b.source.join?.on?.length) {
        const driving = ctx.drivingTable ?? ctx.sources[0]?.table ?? null;
        if (driving && !ctx.sources.some((s) => sameTable(s.table, b.source.table))
            && !sameTable(b.source.table, driving))
          out.push(`branch ${i + 1}: ${shortName(b.source.table)} attaches with no join`);
      }
      // A scope = the branch's value fans out to a row GROUP (UIFPE-104:
      // proliferation stands alone; it is no longer a "grain").
      if (b.scope) {
        if (b.scope.group) {
          if (ctx && !(ctx.rowGroups ?? {})[b.scope.group])
            out.push(`branch ${i + 1}: row group “${b.scope.group}” is not defined in this mapping`);
        } else {
          if (!b.scope.column)
            out.push(`branch ${i + 1}: scope column is required`);
          if (b.scope.op !== "else" && !b.scope.values?.length)
            out.push(`branch ${i + 1}: scope needs at least one value`);
        }
      }
    });
    // Two branches with the SAME discriminator condition need DISTINCT
    // scopes — otherwise both CTEs match the same rows and the strict-pick
    // COALESCE contract breaks.
    const byCond = new Map<string, number>();
    for (const b of col.branches ?? []) {
      const k = (b.condition ?? "").trim().split(" (")[0] + "|" +
        JSON.stringify(b.scope ?? null);
      byCond.set(k, (byCond.get(k) ?? 0) + 1);
    }
    for (const [k, n] of byCond)
      if (n > 1)
        out.push(`${n} branches share condition "${k.split("|")[0]}" with the same scope — give them distinct scopes or merge them`);
    const elseScopes = (col.branches ?? []).filter((b) => {
      if (!b.scope) return false;
      const r = resolveScope(b.scope, ctx?.rowGroups ?? {});
      return r.op === "else" || r.not;
    }).length;
    if (elseScopes > 1)
      out.push(`only one branch can scope to "all remaining rows" (found ${elseScopes})`);
  }
  if (col.kind === "nested") {
    if (!col.joinKey) out.push("join key is required");
    if (!col.fields?.length) out.push("at least one field is required");
    const sortKeys = col.fields?.filter((f) => f.sortKey).length ?? 0;
    // MULTI-key sorts are the platform norm (marketingCopy sorts by three;
    // UIFPE-262 requires the pick's partition to be covered) — the old
    // exactly-one rule predated them. Zero keys stays a blocker: an
    // unsorted array churns the canonical hash every run (A8).
    if (sortKeys < 1)
      out.push("at least one field must be a sort key — array order feeds "
        + "the canonical hash");
    const pick = col.pick;
    if (pick) {
      const names = new Set((col.fields ?? []).map((f) => f.name));
      const sk = new Set((col.fields ?? [])
        .filter((f) => f.sortKey).map((f) => f.name));
      for (const pf of pick.partitionBy ?? []) {
        if (!names.has(pf))
          out.push(`pick partitions by "${pf}", which is not a field`);
        else if (!sk.has(pf))
          out.push(`pick partitions by "${pf}" — make it a sort key so the `
            + "array order stays total (A8)");
      }
      if (!(pick.partitionBy ?? []).length)
        out.push("the pick declares no partition — one arbitrary row would "
          + "survive per entity");
      if (!(pick.orderBy ?? []).length)
        out.push("the pick declares no ordering — the kept row would be "
          + "arbitrary");
    }
    const fieldNames = new Map<string, number>();
    for (const f of col.fields ?? [])
      if (f.name) fieldNames.set(f.name, (fieldNames.get(f.name) ?? 0) + 1);
    for (const [n, c] of fieldNames)
      if (c > 1) out.push(`field name "${n}" is used ${c} times`);
    col.fields?.forEach((f, i) => {
      if (!f.name) out.push(`field ${i + 1}: name is required`);
      if (!f.source?.table) out.push(`field ${i + 1}: source table is required`);
      if (!f.source?.column) out.push(`field ${i + 1}: source column is required`);
      out.push(...transformProblems(f.transform, tfms, `field ${i + 1}`));
    });
    // Fields spanning tables: the first field's table drives the list; every
    // other table must at least be REGISTERED as a source, or the compiler
    // has no alias for it. Since UIFPE-219 the Sources step carries tables
    // only — the attachment is this column's own join key — so the message
    // names the registration, not ON conditions it can no longer collect.
    // (Add flow only: the modify flow edits registered lineage the compiler
    // already knows.)
    if (ctx?.sources.length) {
      const tables = [...new Set((col.fields ?? [])
        .map((f) => f.source?.table).filter((t): t is string => !!t))];
      tables.slice(1).forEach((tb) => {
        if (!ctx.sources.some((s) => sameTable(s.table, tb)))
          out.push(`${shortName(tb)}: fields of this list read it, but it is not one of the task's source tables — add it in Sources`);
      });
    }
  }
  return out;
}
