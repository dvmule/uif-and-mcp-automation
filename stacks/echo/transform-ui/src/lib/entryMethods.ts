// Entry methods for the ONE wizard (UIFPE-219): how a task starts is a home
// page decision, not a wizard step. Import and AI-draft both produce a
// seeded WizardState; the home page saves it as the draft and opens the
// wizard at the Columns step — from there every task looks the same
// (validation, previews, review) whatever door it came in by.
import {
  WizardState, emptyWizard, normalizeColumns,
} from "./types";
import { REGISTERED } from "./repo";
import { findMaster } from "./masterSchema";
import { qualifyTable } from "./silverCatalog";
import {
  pipelineFor, DEFAULT_FREQUENCY, DEFAULT_WEEKLY_DAYS, defaultStartTimeFor,
} from "./pipelines";

// Generate-with-AI draft endpoint (UIFPE-102) — the gold-ai-draft Lambda,
// Bedrock via IAM. The model only DRAFTS intake JSON; it lands in the Columns
// step where validation, compiled-SQL previews and human review apply
// unchanged (ADR-0006 D6). The deploy passes Terraform's `ai_draft_url`
// output to the build.
const AI_DRAFT_URL: string = import.meta.env.VITE_AI_DRAFT_URL ?? "";

const scheduleFor = (table: string | null | undefined) => {
  // keyed by the ENTITY's gold_id (= the table's own name), never by domain —
  // a domain can carry several pipelines with different schedules (UIFPE-252)
  const pipe = pipelineFor(table?.split(".").pop() ?? "");
  const freq = pipe?.frequency ?? DEFAULT_FREQUENCY;
  return {
    frequency: freq,
    startTime: pipe?.startTime ?? defaultStartTimeFor(freq),
    weeklyDays: pipe?.weeklyDays ?? [...DEFAULT_WEEKLY_DAYS],
  };
};

/** Seed a task from a mapping file — a registered *.mapping.json, or a task
 *  envelope downloaded from the Review step. Never submits anything.
 *
 *  INTENT is derived, not assumed. An envelope states its own intent (a
 *  downloaded modify task round-trips as a modify); a bare mapping file is
 *  read against what is already registered:
 *    · no column exists yet            -> add
 *    · every column already exists      -> modify (re-baselining those
 *                                          attributes, e.g. the registered
 *                                          mapping imported to edit in place)
 *    · SOME exist                       -> REFUSED, naming the collision.
 *  The refusal is the honest answer: one task carries one intent, so a mixed
 *  file would either dead-end at the Columns gate ("already exists on the
 *  destination table") or, as a modify, ask the compiler to edit entries that
 *  are not there. Two imports do what one cannot. */
export async function seedFromImport(file: File): Promise<WizardState> {
  const raw = JSON.parse(await file.text());
  const intake = raw.columns ? raw : raw.intake;
  if (!intake?.domain || !intake?.table || !intake?.columns?.length)
    throw new Error(
      "this file is not an intake mapping — it needs domain, table and columns");
  const columns = normalizeColumns(intake.columns);
  if (!columns.length)
    throw new Error("no usable columns — every entry needs a goldColumnName "
      + "and a kind of scalar/conditional/nested");

  const reg = REGISTERED.find((r) => r.table === intake.table);
  const registeredNames = new Set(
    (reg?.columns ?? []).map((c) => c.goldColumnName));
  const existing = columns.filter((c) => registeredNames.has(c.goldColumnName));
  const fresh = columns.filter((c) => !registeredNames.has(c.goldColumnName));

  // UIFPE-260: a DRAFT entity being re-scaffolded imports as a NEW-TABLE
  // task — its intake legitimately carries every column, because the draft
  // artifacts are superseded input, not conflicts (the scaffolder's own
  // rule). The file declares the shape itself (entityKey +
  // drivingSilverTable); a LIVE table's import keeps the
  // one-task-one-intent rule below.
  const entityName = (intake.table ?? "").split(".").pop() ?? "";
  const lifecycle = intake.domain
    ? findMaster(intake.domain, entityName)?.metadata?.lifecycle : undefined;
  const rescaffold = Boolean(
    intake.drivingSilverTable && intake.entityKey?.length
    && (!reg || lifecycle === "draft"));

  // An envelope's own intent wins — that is what makes a task downloaded
  // from Review importable again as the same task — but it is still CHECKED
  // against what is registered, because the intent and the columns have to
  // agree for the compiler to serve the task at all.
  const names = (cols: typeof columns) =>
    cols.slice(0, 3).map((c) => c.goldColumnName).join(", ")
    + (cols.length > 3 ? ` and ${cols.length - 3} more` : "");
  let intent: "add" | "modify" = raw.intent === "modify" ? "modify" : "add";
  if (intent === "modify" && !reg)
    // Nothing to modify: the compiler edits entries in a registered mapping,
    // and there is no mapping for this table. Silently turning this into a
    // create would be a surprise, not a convenience.
    throw new Error(
      `this task changes attributes on ${intake.table}, but no mapping is `
      + "registered for that table — check the table name, or import it as a "
      + "new table (a file with no intent is read as new attributes)");
  if (reg && !rescaffold) {
    if (intent === "modify" && fresh.length)
      // A modify task may only carry attributes that exist: the compiler
      // edits the registered entry in place and raises on a stranger.
      throw new Error(
        `this task changes ${intake.table}, but ${names(fresh)} `
        + `${fresh.length === 1 ? "is" : "are"} not registered on it — a `
        + "change task can only carry attributes that already exist; import "
        + "the new ones separately");
    if (!raw.intent) {
      if (existing.length && fresh.length)
        throw new Error(
          `this file mixes new and existing attributes on ${intake.table} — `
          + `${names(existing)} already exist, ${fresh.length} do not. One `
          + "task carries one intent, so import the new attributes and the "
          + "changes separately");
      if (existing.length) intent = "modify";
    }
  }

  if (rescaffold) intent = "add";
  return {
    ...emptyWizard(),
    method: "import",
    intent,
    domain: intake.domain,
    destinationTable: intake.table,
    // the new-table identity step works on the QUALIFIED silver table; a
    // re-scaffold intake carries the bare Oracle-style name
    drivingTable: rescaffold
      ? (qualifyTable(intake.drivingTable ?? "", intake.domain)
         ?? intake.drivingTable ?? null)
      : intake.drivingTable ?? reg?.drivingTable ?? null,
    drivingFilter: intake.drivingFilter ?? "",
    entityKey: intake.entityKey ?? reg?.entityKey ?? [],
    rowGroups: intake.rowGroups ?? reg?.rowGroups ?? {},
    transforms: intake.transforms ?? reg?.transforms ?? {},
    isNewTable: !reg || rescaffold,
    // The modify flow names the attribute it is editing; a multi-column
    // import names the first (the Columns list carries them all).
    targetColumn: intent === "modify" && !rescaffold
      ? (existing[0]?.goldColumnName ?? columns[0].goldColumnName) : null,
    columns,
    // A bare intake (no envelope) carries no source graph — seed the driving
    // table plus every nested-field LOOKUP table (UIFPE-262), exactly what
    // the guided flow declares, so the Sources gate isn't a dead end.
    sources: raw.sourceGraph
      ?? ((intake.drivingTable || rescaffold)
        ? [{ table: qualifyTable(intake.drivingTable ?? "", intake.domain)
               ?? intake.drivingTable,
             eav: false, joinType: "left" as const, joins: [] },
           ...[...new Map(columns
             .filter((c: any) => c.kind === "nested")
             .flatMap((c: any) => (c.fields ?? [])
               .map((f: any) => f?.source?.table ?? ""))
             .filter((t: string) => t && t.split(".").pop()!.toLowerCase()
               !== (intake.drivingTable ?? "").split(".").pop()!.toLowerCase())
             .map((t: string) => [t.toLowerCase(), t] as [string, string]))
             .values()]
             .map((t: string) => ({
               table: qualifyTable(t.split(".").pop()!, intake.domain) ?? t,
               eav: false, joinType: "left" as const, joins: [] }))]
        : []),
    refreshHistorical: raw.refresh?.historical ?? true,
    refreshMode: raw.refresh?.mode === "silent" ? "silent" : "emit",
    requestedBy: intake.batch?.requestedBy ?? "",
    targetDate: intake.batch?.targetDate ?? null,
    ...scheduleFor(intake.table),
  };
}

/** Seed a task from a plain-language description. The draft opens for review
 *  like any import — nothing about it is pre-approved. */
export async function seedFromAI(description: string): Promise<WizardState> {
  const reg = REGISTERED[0];
  const r = await fetch(AI_DRAFT_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      description,
      domain: reg?.domain, table: reg?.table,
      drivingTable: reg?.drivingTable,
      rowGroups: reg?.rowGroups,
      transforms: reg?.transforms,
    }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`);
  return {
    ...emptyWizard(),
    method: "ai",
    intent: "add",
    domain: reg?.domain ?? null,
    destinationTable: reg?.table ?? null,
    drivingTable: reg?.drivingTable ?? null,
    entityKey: reg?.entityKey ?? [],
    rowGroups: reg?.rowGroups ?? {},
    transforms: reg?.transforms ?? {},
    isNewTable: false,
    targetColumn: null,
    columns: normalizeColumns(data.columns),
    sources: [],
    ...scheduleFor(reg?.table),
  };
}

/** AI draft for a DERIVED column's query (UIFPE-219): same service, same
 *  governance — the model only DRAFTS; the text lands in the authoring
 *  panel where the advisory checks, the wrap preview, human review and the
 *  Python battery all apply unchanged. Nothing it returns is executed. */
export async function draftDerivedSql(description: string, ctx: {
  domain?: string | null; table?: string | null;
  drivingTable?: string | null; columnName?: string;
  dataType?: string; sources: string[];
  entityKey: { drivingColumn: string }[];
}): Promise<{ sql: string;
              keyedBy: { queryColumn: string; entityColumn: string }[];
              valueColumn?: string }> {
  const r = await fetch(AI_DRAFT_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      mode: "derivedSql",
      description,
      domain: ctx.domain, table: ctx.table, drivingTable: ctx.drivingTable,
      columnName: ctx.columnName, dataType: ctx.dataType,
      knownTables: ctx.sources,
      entityKey: ctx.entityKey.map((k) => k.drivingColumn),
    }),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok)
    throw new Error(data?.error ?? `the draft service answered HTTP ${r.status}`);
  // shape-check before anything touches the wizard draft: a skewed or older
  // deployed service answering the column shape must surface as an error,
  // never silently poison the block into the older-format recovery path
  if (typeof data?.sql !== "string" || !data.sql.trim()
      || !Array.isArray(data.keyedBy))
    throw new Error("the draft service answered in an unexpected shape — "
      + "it may be an older deployment; try again after the next deploy");
  return data;
}
