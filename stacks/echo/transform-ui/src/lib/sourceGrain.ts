// How each Silver source ATTACHES to the entity key — derived, never authored.
//
// The runtime already models this: every manifest source declares a
// `key_mapping` from a changed source row to the Gold PK column(s) it affects,
// and a source binding only PART of the key fans out across the rest (ADR-0003
// D1). The intake contract does not state that directly — it states the JOINS,
// per attribute, as ON pairs. Coverage is therefore DERIVABLE: an ON pair whose
// left side is a driving-table key column covers that key column.
//
// The unit is the ATTACHMENT — (table, join shape) — not the table. The product
// entity proves why: EGO_MTL_SY_ITEMS_EXT_B is read at three different grains in
// one mapping (55 attributes join it on the item only, 10 on item AND org, 22
// array fields state no join at all). Aggregating coverage per table unions
// those and overstates reach for the majority of uses.
//
// `key_mapping` is then the INTERSECTION of an attachment's coverage across a
// table's stated attachments — the narrowest coverage, which yields the widest
// fan-out and therefore the safe superset of rows to invalidate. Deriving it
// reproduces the hand-written manifest (see the test), which is the point: a
// hand-narrowed key_mapping would silently serve stale values, and nothing in
// the pipeline currently stops someone writing one.
//
// Deliberately NOT computed here: the fan-out MAGNITUDE ("up to 1.6M rows").
// That needs row counts per key value, which the committed catalog snapshot does
// not carry. Coverage is a proportion of key columns; magnitude arrives with the
// first live-data read.

import { sameTable } from "./types";
import type { EntityKeyPair, MappingColumn, SourceRef } from "./types";

const shortName = (t: string) => t.split(".").pop() ?? t;

/** Grouping identity for a source table. A DATABASE-QUALIFIED name keeps its
 *  qualifier, because the same table name in two Silver databases is two
 *  different tables (sameTable() has always known this; grouping by the bare
 *  name alone merged them into one card and left the second with none). Bare
 *  names — how every registered mapping writes them — group by the bare name
 *  as before. */
const tableKey = (t: string) => t.toLowerCase();
const up = (s: string | undefined | null) => (s ?? "").toUpperCase();

/** How one attachment reaches the entity key. */
export type AttachShape =
  /** The entity's spine. Its own columns ARE the grain. */
  | "driving"
  /** Join covers every key column — at most one source row per Gold row. */
  | "full-key"
  /** Join covers some key columns — the value repeats across the rest. */
  | "partial-key"
  /** No join stated, and not the driving table. */
  | "undeclared";

export interface SourceAttachment {
  /** Table as written in the mapping (bare PDH or qualified Silver name). */
  table: string;
  shortName: string;
  shape: AttachShape;
  /** Gold key columns this attachment's join covers. */
  covers: string[];
  /** Gold key columns it does NOT cover — the value repeats across these. */
  repeatsAcross: string[];
  /** ON pairs whose left side is neither the driving table nor a key column: a
   *  chain through an intermediate table, which this pass does not follow. */
  indirect: boolean;
  joinType: "left" | "inner" | null;
  /** Attributes reading the table through THIS attachment. */
  usedBy: number;
  /** A few of their names, for "who depends on this". */
  examples: string[];
  /** Distinct non-empty filters across those attributes — what narrows a
   *  many-rows-per-key source to one row (an attribute group, a language). */
  filters: string[];
  /** Uses that stated no filter. Not necessarily wrong — a 1:1 lookup needs
   *  none — but it is the difference between "resolved" and "not stated". */
  usesWithoutFilter: number;
}

interface Use {
  table: string;
  attribute: string;
  filter: string | null;
  join: SourceRef["join"];
}

/** Every place an attribute reads a table. Array FIELDS carry no join in the
 *  contract (NestedField.source is table/column/filter only), so they surface
 *  as attachments with nothing stated — which is how they should read: the
 *  array's own joinKey covers them at runtime, and the intake has nowhere to
 *  record that per field. */
function collectUses(columns: MappingColumn[]): Use[] {
  const out: Use[] = [];
  for (const col of columns) {
    const n = col.goldColumnName;
    if (col.kind === "scalar") {
      if (col.source?.table)
        out.push({ table: col.source.table, attribute: n,
                   filter: col.source.filter ?? null, join: col.source.join ?? null });
    } else if (col.kind === "conditional") {
      (col.branches ?? []).forEach((b, i) => {
        if (b.source?.table)
          out.push({ table: b.source.table, attribute: `${n} (branch ${i + 1})`,
                     filter: b.source.filter ?? null, join: b.source.join ?? null });
      });
    } else {
      for (const f of col.fields ?? [])
        if (f.source?.table)
          out.push({ table: f.source.table, attribute: `${n}.${f.name}`,
                     filter: f.source.filter ?? col.filter ?? null, join: null });
    }
  }
  return out;
}

export interface GrainInput {
  drivingTable?: string | null;
  entityKey?: EntityKeyPair[];
  columns: MappingColumn[];
}

/** Coverage of one use's join, in Gold key-column terms. */
function coverageOf(u: Use, goldFor: Map<string, string>, driving: string | null) {
  const covers = new Set<string>();
  let indirect = false;
  for (const on of u.join?.on ?? []) {
    const gold = goldFor.get(up(on.leftColumn));
    if (gold && (!driving || sameTable(on.leftTable, driving))) covers.add(gold);
    else indirect = true;
  }
  return { covers, indirect };
}

/**
 * One row per distinct (table, join shape). Ordered driving-table first, then
 * the attachments that reach least of the key — the expensive ones surface at
 * the top.
 */
export function deriveSourceAttachments(m: GrainInput): SourceAttachment[] {
  const key = m.entityKey ?? [];
  const keyGold = key.map((p) => p.goldColumn);
  const driving = m.drivingTable ?? null;
  const goldFor = new Map(key.map((p) => [up(p.drivingColumn), p.goldColumn]));

  // group by table + the exact set of key columns its join covers
  const groups = new Map<string, { uses: Use[]; covers: string[]; indirect: boolean }>();
  for (const u of collectUses(m.columns)) {
    const { covers, indirect } = coverageOf(u, goldFor, driving);
    const ordered = keyGold.filter((g) => covers.has(g));
    const k = `${tableKey(u.table)}|${ordered.join(",")}|${u.join ? "j" : "-"}`;
    const g = groups.get(k) ?? { uses: [], covers: ordered, indirect: false };
    g.uses.push(u);
    g.indirect = g.indirect || indirect;
    groups.set(k, g);
  }

  const rows: SourceAttachment[] = [];
  for (const g of groups.values()) {
    const table = g.uses[0].table;
    const isDriving = !!driving && sameTable(table, driving);
    const stated = g.uses.some((u) => !!u.join?.on?.length);

    const shape: AttachShape = isDriving ? "driving"
      : !stated ? "undeclared"
      : key.length && g.covers.length === key.length ? "full-key"
      : g.covers.length ? "partial-key"
      : "undeclared";

    rows.push({
      table, shortName: shortName(table), shape,
      covers: g.covers,
      repeatsAcross: isDriving ? [] : keyGold.filter((x) => !g.covers.includes(x)),
      indirect: g.indirect,
      joinType: g.uses.find((u) => u.join?.type)?.join?.type ?? null,
      usedBy: g.uses.length,
      examples: g.uses.slice(0, 3).map((u) => u.attribute),
      filters: [...new Set(g.uses.map((u) => (u.filter ?? "").trim()).filter(Boolean))],
      usesWithoutFilter: g.uses.filter((u) => !(u.filter ?? "").trim()).length,
    });
  }

  const rank = (s: AttachShape) =>
    s === "driving" ? 0 : s === "undeclared" ? 1 : s === "partial-key" ? 2 : 3;
  return rows.sort((a, b) =>
    rank(a.shape) - rank(b.shape)
    || b.repeatsAcross.length - a.repeatsAcross.length
    || a.shortName.localeCompare(b.shortName)
    || b.usedBy - a.usedBy);
}

/** Attachments grouped by table, so a screen can show that one table is read at
 *  several grains — the thing a per-table view hides. */
export function attachmentsByTable(rows: SourceAttachment[]):
  { table: string; shortName: string; attachments: SourceAttachment[] }[] {
  const by = new Map<string, SourceAttachment[]>();
  for (const r of rows) {
    const k = tableKey(r.table);
    (by.get(k) ?? by.set(k, []).get(k)!).push(r);
  }
  return [...by.values()].map((attachments) => ({
    table: attachments[0].table, shortName: attachments[0].shortName, attachments }));
}

/** Plain-language reading of what a change to this source costs, written for
 *  someone who owns the business meaning rather than the join graph. */
export function fanOutSentence(a: SourceAttachment): string {
  switch (a.shape) {
    case "driving":
      return "Decides which rows exist. One changed row affects its own Gold row.";
    case "full-key":
      return "Attaches on the whole key, so one changed row affects one Gold row.";
    case "partial-key":
      return `Attaches on part of the key, so the value repeats across `
        + `${a.repeatsAcross.join(" and ")} — one changed row affects every Gold `
        + `row sharing ${a.covers.join(" and ")}.`;
    case "undeclared":
      return "States no join, so how it reaches the key is not recorded here.";
  }
}

/** The Sources CARD model (UIFPE-219): one entry per table with ONE plain
 *  change-impact sentence — what the wizard's Sources step and the standalone
 *  Sources view both render. The per-attachment analysis stays available as
 *  `odd` for the expandable detail, shown only when the table is genuinely
 *  read in more than one way. */
export interface TableSummary {
  table: string;
  shortName: string;
  primary: boolean;
  /** How many gold COLUMNS read this table — distinct, so it matches the list
   *  a screen expands. (An attachment's own `usedBy` counts source USES: a
   *  conditional's branches and a list's fields each count once, which is the
   *  right number per attachment and the wrong number per table.) */
  usedBy: number;
  /** The distinct gold columns themselves — a screen that LISTS them renders
   *  from here, so the count and the list are the same fact. */
  columns: string[];
  examples: string[];
  /** One plain sentence: what a change to this table costs. */
  sentence: string;
  /** Per-attachment detail — non-empty only when the shapes differ. */
  odd: SourceAttachment[];
}

/** Distinct gold columns that read each table, keyed like the groups below.
 *  Counted from the columns themselves so the number always equals the rows a
 *  screen renders when it lists them. */
function columnsPerTable(columns: MappingColumn[]): Map<string, string[]> {
  const out = new Map<string, Set<string>>();
  const add = (table: string | undefined, name: string) => {
    if (!table) return;
    const k = tableKey(table);
    (out.get(k) ?? out.set(k, new Set()).get(k)!).add(name);
  };
  for (const col of columns) {
    const n = col.goldColumnName;
    if (col.kind === "scalar") add(col.source?.table, n);
    else if (col.kind === "conditional")
      (col.branches ?? []).forEach((b) => add(b.source?.table, n));
    else (col.fields ?? []).forEach((f) => add(f.source?.table, n));
  }
  return new Map([...out].map(([k, v]) => [k, [...v]]));
}

export function tableSummaries(m: GrainInput): TableSummary[] {
  const grouped = attachmentsByTable(deriveSourceAttachments(m));
  const perTable = columnsPerTable(m.columns);
  return grouped.map(({ table, shortName, attachments }) => {
    const primary = attachments.some((a) => a.shape === "driving");
    const cols = perTable.get(tableKey(table)) ?? [];
    const usedBy = cols.length;
    const examples = cols.slice(0, 3);
    const shapes = new Set(attachments.map((a) => a.shape));
    let sentence: string;
    if (primary) {
      sentence = "Decides which rows exist — one changed row affects its own row.";
    } else if (shapes.size === 1) {
      const a = attachments[0];
      sentence = a.shape === "full-key"
        ? "One changed row here affects one row of the gold table."
        : a.shape === "partial-key"
          ? `One changed row here can affect many rows — its value repeats `
            + `across ${a.repeatsAcross.join(" and ")}.`
          : "How it reaches each row is declared on each attribute that reads "
            + "it (in the Columns step).";
    } else {
      sentence = "Read in more than one way — the cost of a change depends on "
        + "which attribute reads it (see each attribute).";
    }
    return { table, shortName, primary, usedBy, columns: cols, examples,
             sentence, odd: shapes.size > 1 ? attachments : [] };
  }).sort((a, b) => Number(b.primary) - Number(a.primary)
    || a.shortName.localeCompare(b.shortName));
}

/** Whether a many-rows-per-key source has been narrowed to one row. Factual —
 *  the caller phrases it, because "no filter" is correct for a 1:1 lookup and a
 *  defect for an attribute store. */
export function disambiguation(a: SourceAttachment):
  "not-needed" | "every-use-filtered" | "some-uses-unfiltered" | "none-stated" {
  if (a.shape === "driving") return "not-needed";
  if (!a.filters.length) return "none-stated";
  return a.usesWithoutFilter ? "some-uses-unfiltered" : "every-use-filtered";
}

/**
 * The reverse mapping the runtime needs for one source TABLE: a changed row ->
 * the Gold key columns it can affect.
 *
 * The intersection of the table's stated attachments, not the union. A table
 * read at two grains can affect rows through either, so only the key columns
 * EVERY stated attachment covers can be relied on to pin a row down; the rest
 * must fan out. Attachments that state no join contribute nothing and are
 * excluded rather than treated as covering nothing — otherwise one array field
 * would collapse the mapping to empty.
 */
export function keyMappingForTable(attachments: SourceAttachment[],
                                   key: EntityKeyPair[]): Record<string, string> {
  const out: Record<string, string> = {};
  if (attachments.some((a) => a.shape === "driving")) {
    for (const p of key) out[p.goldColumn] = p.drivingColumn;
    return out;
  }
  const stated = attachments.filter((a) => a.shape !== "undeclared");
  if (!stated.length) return out;
  for (const p of key)
    if (stated.every((a) => a.covers.includes(p.goldColumn)))
      out[p.goldColumn] = p.drivingColumn;
  return out;
}
