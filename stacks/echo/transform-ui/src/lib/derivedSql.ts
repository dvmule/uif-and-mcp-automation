// Authoring checks for a derived column's query (ADR-0008, UIFPE-219).
//
// The AUTHORITATIVE battery is Python (derivation_chain.py): a real parser
// (sqlglot) runs at submit and in CI, and nothing merges past it. This
// module is the browser's advisory mirror — the same refusals, approximated
// with token scanning so the author hears about a problem while typing
// instead of minutes later from CI. Where an approximation cannot be exact
// (no parser here), it errs toward SILENCE on maybe-fine queries and
// speaks only when the battery would certainly refuse; the panel says so.
//
// One deliberate divergence: the battery checks referenced tables against
// the entity's REGISTERED manifest sources; the wizard checks against the
// task's declared Sources step, because for an intake that is what the
// compiler will register — and "add the table to Sources first" is the fix
// the author can actually perform in the wizard.

import type { DerivationBlock, MappingColumn, SourceDecl } from "./types";

// ONE left-to-right pass: a '--' inside a string is consumed by the string
// alternative, never mistaken for a comment — sequential replaces got that
// wrong in both directions (found adversarially: `LIKE '%--%'` ate its own
// closing quote and false-flagged the next line's literal as a write).
const NOISE_RE =
  /('(?:[^'\\]|\\.)*')|("(?:[^"\\]|\\.)*")|(`[^`]*`)|(--[^\n]*)|(\/\*[\s\S]*?\*\/)/g;

/** SQL with comments and string/backtick literals blanked — token scans
 *  must never match inside a comment or a quoted value. */
export function stripNoise(sql: string): string {
  return sql.replace(NOISE_RE, (_, sq, dq, bt) =>
    sq ? "''" : dq ? '""' : bt ? "``" : " ");
}

/** Table names the query selects FROM/JOINs, with the query's own CTE names
 *  excluded (they are not sources). Database-qualified names are kept
 *  qualified — the battery refuses them, so the panel must too.
 *
 *  Regex, not a parser, so it errs toward SILENCE: function-call interiors
 *  are blanked first (extract(YEAR FROM x) and trim(BOTH … FROM y) carry a
 *  FROM that names no table), keyword pseudo-sources (VALUES, LATERAL,
 *  UNNEST) are skipped, and the second table of a comma join is missed —
 *  the battery catches that one at submit. */
export function referencedTables(sql: string): string[] {
  let clean = stripNoise(sql);
  // blank `ident( … )` interiors to a fixpoint — nested calls included.
  // Bare parens (subqueries) are NOT touched, and neither are the
  // STRUCTURAL keywords whose parens hold real FROM clauses: `x AS (…)` is
  // a CTE body, `… UNION (…)` a set-operation branch — eating those would
  // blank the very clauses this scan exists to read.
  const STRUCTURAL = /^(as|union|intersect|except|and|or|not|when|then|else|on)$/i;
  for (let i = 0; i < 20; i++) {
    const next = clean.replace(/\b([A-Za-z_]\w*)\s*\(([^()]*)\)/g,
      (m, name, inner) => STRUCTURAL.test(name)
        ? m : m.replace(inner, " ".repeat(inner.length)));
    if (next === clean) break;
    clean = next;
  }
  const ctes = new Set<string>();
  for (const m of clean.matchAll(
    /(?:\bwith\s+|,\s*)([A-Za-z_][\w]*)\s*(?:\([^)]*\))?\s+as\s*\(/gi))
    ctes.add(m[1].toLowerCase());
  const NOT_TABLES = new Set(["values", "unnest", "lateral", "select"]);
  const tables = new Set<string>();
  for (const m of clean.matchAll(/\b(?:from|join)\s+([A-Za-z_][\w.]*)/gi)) {
    const name = m[1];
    if (!ctes.has(name.toLowerCase()) && !NOT_TABLES.has(name.toLowerCase()))
      tables.add(name);
  }
  return [...tables];
}

const WRITE_RE =
  /\b(insert|update|delete|merge|create|drop|alter|truncate|grant)\b/i;
// derivation_chain.py's stability scan: typed nodes + _NONDET_ANONYMOUS
const NONDET_RE = new RegExp(
  "\\b(rand|randn|uuid|now|random|shuffle|monotonically_increasing_id|"
  + "input_file_name|spark_partition_id|localtimestamp|current_timezone|"
  + "java_method|reflect)\\s*\\(|"
  + "\\b(current_timestamp|current_date|current_time)\\b|"
  + "\\bunix_timestamp\\s*\\(\\s*\\)", "i");
const COLLECT_RE = /\b(collect_list|collect_set|array_agg)\s*\(/i;
const SORTED_RE = /\b(array_sort|sort_array)\s*\(/i;

// contract columns match case-SENSITIVELY: the battery compares the exact
// strings against the parser's output names
const word = (s: string) =>
  new RegExp(`\\b${s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`);

/** The entity's registered runtime views, as the battery sees them:
 *  `aliases` is what a query may read; `aliasByTable` maps a physical
 *  table's bare name (upper) to the alias that reads it — so the panel can
 *  say "this entity reads X as items_ext" instead of a bare refusal. */
export interface ManifestView {
  aliases: Set<string>;
  aliasByTable: Map<string, string>;
}

/** What the battery would refuse — phrased as the fix, not the rule.
 *  The battery accepts a query reading the entity's registered ALIASES, so
 *  the mirror must too — a registered query showing red here would teach
 *  authors to ignore the panel. */
export function derivedSqlProblems(col: MappingColumn,
                                   block: DerivationBlock,
                                   sources: SourceDecl[],
                                   manifest: ManifestView = {
                                     aliases: new Set(),
                                     aliasByTable: new Map() }): string[] {
  const name = col.goldColumnName || "(unnamed)";
  if (name.includes("."))
    return ["a struct member cannot carry its own query — put it on the "
            + "parent column"];

  const out: string[] = [];
  const sql = (block.sql ?? "").trim();
  if (!sql) return ["the query is empty — paste or draft the SELECT"];
  const clean = stripNoise(sql);

  if (sql.endsWith(";"))
    out.push("remove the trailing ';' — the query is embedded inside the "
      + "gold query, where a terminator breaks it");
  else if (clean.includes(";"))
    out.push("one statement only — the contract is a single SELECT");
  if (!/^\s*\(*\s*(select|with)\b/i.test(clean))
    out.push("the query must be one read-only SELECT (or WITH … SELECT)");
  const w = clean.match(WRITE_RE);
  if (w) out.push(`'${w[1].toUpperCase()}' has no place here — derived `
    + "queries are read-only");
  const nd = clean.match(NONDET_RE);
  if (nd) out.push(`${(nd[1] ?? nd[2] ?? "unix_timestamp()")} would change `
    + "between identical runs — derived values must be stable");
  if (COLLECT_RE.test(clean) && !SORTED_RE.test(clean))
    out.push("a collect without a stated ordering produces differently-"
      + "ordered lists on identical runs — wrap it in array_sort()/sort_array()");

  // every table read must be one the battery will accept: a registered
  // runtime alias, or — for sources this task newly declares — the bare
  // name. A physical table the manifest already reads under an alias is
  // named by the alias to use, not just refused.
  const declared = new Set(sources.map(
    (s) => (s.table.split(".").pop() ?? s.table).toUpperCase()));
  for (const t of referencedTables(sql)) {
    const up = t.toUpperCase();
    if (t.includes("."))
      out.push(`'${t}' is database-qualified — sources are read by their `
        + "bare registered names");
    else if (manifest.aliases.has(up)) continue;
    else if (manifest.aliasByTable.has(up))
      out.push(`this entity reads ${t} as '${manifest.aliasByTable.get(up)}'`
        + " — use that name in the query");
    else if (!declared.has(up))
      out.push(`the query reads '${t}', which is not in this task's Sources `
        + "— add it at the Sources step first");
  }

  // the declared contract must be visible in the query's output. Without a
  // parser this is a presence check — enough to catch the typo case. It
  // runs on a copy where backtick QUOTES are unwrapped (their contents are
  // identifiers, and `parentProduct` is idiomatic Spark), while comments
  // and strings stay blanked.
  const identClean = sql.replace(NOISE_RE, (_, sq, dq, bt: string) =>
    sq ? "''" : dq ? '""' : bt ? bt.slice(1, -1) : " ");
  const keyed = block.keyedBy ?? [];
  if (!keyed.length)
    out.push("declare which output column identifies the entity (keyedBy)");
  for (const k of keyed) {
    const qc = String(k.queryColumn ?? "").trim();
    const ec = String(k.entityColumn ?? "").trim();
    if (!qc || !ec)
      out.push("each key pair needs both halves — the query's column and "
        + "the entity column it matches");
    else if (!word(qc).test(identClean))
      out.push(`'${qc}' does not appear in the query — the key `
        + "column must be one of its outputs");
  }
  const value = String(block.valueColumn ?? "").trim() || name;
  if (keyed.length && !word(value).test(identClean))
    out.push(`'${value}' does not appear in the query — the value column `
      + "must be one of its outputs");
  return out;
}

/** A fresh block for "this value needs its own query": keys pre-filled from
 *  the entity key (the 90% case), value defaulting to the column itself. */
export function emptyDerivation(colName: string,
                                entityKey: { drivingColumn: string }[]):
  DerivationBlock {
  return {
    sql: "",
    keyedBy: (entityKey.length ? entityKey : [{ drivingColumn: "" }])
      .map((k) => ({ queryColumn: k.drivingColumn, entityColumn: k.drivingColumn })),
    valueColumn: colName || undefined,
  };
}
