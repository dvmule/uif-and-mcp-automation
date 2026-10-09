// rowFilter.js — the wizard's row-filter model, as pure functions.
//
// A filter is an ordered list of CLAUSES, combined with AND. Each complete
// clause compiles to ONE Spark SQL boolean string — one entry of the engine's
// `filters` block, which dynamic_mapping applies in turn to the joined frame
// before mapping (engine.apply_filters). Three kinds:
//   { id, kind: "list",      column: "alias.col", idKind: "ppid"|"isbn13"|"other",
//     values: [..], invalid: [{value, reason}], suspicious: [{value, reason}] }
//   { id, kind: "attribute", column: "alias.col", category, op, value, value2 }
//   { id, kind: "custom",    sql }
// `category` is fixed on the attribute clause when it is authored, so the
// compiler needs no type map later (the map may be missing on edit).
//
// The attribute picker offers only columns a person could reasonably pick a
// value for (UIFPE-514, owner decisions 2026-09-22): identifiers by name are
// never offered, text columns wait for the source's cardinality PROFILE and
// are hidden over TEXT_CARDINALITY_CAP, and numbers, dates, timestamps and
// booleans are always offered — see attributeColumnsFor.
//
// Rules the compiled SQL must satisfy (config_validator.py): every column is
// alias-qualified (`product.isbn13` — the reference check skips bare names),
// every function call is on the engine's allowlist (array_contains, size,
// to_date and to_timestamp are), and string literals escape `\` and `'` with
// a BACKSLASH — Spark reads 'O''Brien' as two adjacent literals and
// concatenates them to OBrien.
//
// Nothing here touches React or the API — FilterStep.jsx renders, Wizard.jsx
// owns state; this module owns the rules so they are unit-testable (vitest,
// see __tests__/rowFilter.test.js) without a browser.
import { buildMappingConfig, elementAliasesOf, knownColumns, listColumnsFor, narrowHint, rowShape, tableHeading } from "./feedSources.js";

// Per-condition ceiling for an identifier list. Not a Spark limit (Catalyst
// folds a large IN into an InSet) — a draft is ONE DynamoDB item (400 KB) and
// the list is stored twice (compiled filter + this model), ~165 KB per 5,000.
export const LIST_CAP = 5000;
// Byte ceiling for the whole model (JSON), leaving room for the compiled copy
// and the rest of the draft inside the item cap. Mirrors lifecycle's guard.
export const FILTER_BYTES_CAP = 250_000;

export const ID_KINDS = [
  { id: "ppid", label: "PPID" },
  { id: "isbn13", label: "ISBN-13" },
  { id: "other", label: "Other values" },
];

export const CATEGORIES = ["string", "number", "boolean", "date", "timestamp", "array", "struct", "unknown"];

// validate_structure needs at least one mapping rule, so a filter check run
// before any field is mapped carries this stub — the result is about the
// filter, and the column layer still runs (validation.py gates it on zero
// structure/expression issues).
export const FILTER_CHECK_STUB_MAPPING = { type: "literal", target: "_filter_check", value: 1 };

let idSeq = 0;
export function newClauseId() {
  idSeq += 1;
  return `c_${Date.now().toString(36)}${idSeq.toString(36)}`;
}

const fmt = (n) => Number(n).toLocaleString("en-US");

function splitRef(ref) {
  const s = String(ref || "");
  const i = s.indexOf(".");
  return i < 0 ? [s, ""] : [s.slice(0, i), s.slice(i + 1)];
}

function utf8Bytes(str) {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(str).length;
  return str.length;
}

// ---------------------------------------------------------------- literals

// Spark string literal: backslash-escaped. Backslashes first, then quotes.
export function escapeLiteral(value) {
  const s = String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  return `'${s}'`;
}

// A LIKE pattern whose user text is matched LITERALLY: % and _ inside the
// value are escaped (LIKE's default escape character is the backslash), then
// the pattern is wrapped as a literal. "50%" contains → '%50\\%%'.
export function likeLiteral(value, { prefix = "", suffix = "" } = {}) {
  const s = String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/%/g, "\\%")
    .replace(/_/g, "\\_");
  return escapeLiteral(`${prefix}${s}${suffix}`);
}

const NUMBER_RE = /^-?\d+(\.\d+)?$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$/;

// The SQL literal for a typed value, or null when the value is not valid for
// the category (the preview then shows a placeholder and the clause is
// incomplete). Strings are trimmed: a trailing space in a code is a typo.
export function renderLiteral(category, value) {
  const v = String(value ?? "").trim();
  if (v === "") return null;
  switch (category) {
    case "number":
      return NUMBER_RE.test(v) ? v : null;
    case "date":
      return DATE_RE.test(v) ? `to_date('${v}')` : null;
    case "timestamp": {
      if (DATE_RE.test(v)) return `to_timestamp('${v} 00:00:00')`;
      if (!TIMESTAMP_RE.test(v)) return null;
      const norm = v.replace("T", " ") + (/:\d{2}:\d{2}$/.test(v) ? "" : ":00");
      return `to_timestamp('${norm}')`;
    }
    default:
      return escapeLiteral(v);
  }
}

// ------------------------------------------------------------ column types

// The operator family a Glue type string belongs to. Unknown/absent → the
// string operators plus an honest hint in the UI.
export function glueTypeCategory(glueType) {
  const t = String(glueType ?? "").trim().toLowerCase();
  if (!t) return "unknown";
  if (/^(string|varchar|char)\b/.test(t)) return "string";
  if (/^(tinyint|smallint|int|integer|bigint|float|double|decimal|numeric|long|short|byte)\b/.test(t))
    return "number";
  if (t === "boolean") return "boolean";
  if (t === "date") return "date";
  if (t.startsWith("timestamp")) return "timestamp";
  if (t.startsWith("array<")) return "array";
  if (t.startsWith("struct<") || t.startsWith("map<")) return "struct";
  return "unknown";
}

export function arrayElementCategory(glueType) {
  const m = String(glueType ?? "").trim().toLowerCase().match(/^array<(.*)>$/);
  return m ? glueTypeCategory(m[1]) : "unknown";
}

const OPS = {
  eq: { label: "equals", arity: 1 },
  neq: { label: "does not equal", arity: 1 },
  contains: { label: "contains", arity: 1 },
  starts_with: { label: "starts with", arity: 1 },
  ends_with: { label: "ends with", arity: 1 },
  gt: { label: "is greater than", arity: 1 },
  gte: { label: "is at least", arity: 1 },
  lt: { label: "is less than", arity: 1 },
  lte: { label: "is at most", arity: 1 },
  between: { label: "is between", arity: 2 },
  on: { label: "is on", arity: 1 },
  before: { label: "is before", arity: 1 },
  after: { label: "is after", arity: 1 },
  on_or_after: { label: "is on or after", arity: 1 },
  on_or_before: { label: "is on or before", arity: 1 },
  is_true: { label: "is true", arity: 0 },
  is_false: { label: "is false", arity: 0 },
  has_value: { label: "contains the value", arity: 1 },
  is_empty: { label: "is empty", arity: 0 },
  not_empty: { label: "is not empty", arity: 0 },
  is_null: { label: "is null", arity: 0 },
  not_null: { label: "is not null", arity: 0 },
};

const STRING_OPS = ["eq", "neq", "contains", "starts_with", "ends_with", "is_null", "not_null"];
const DATE_OPS = ["on", "before", "after", "on_or_after", "on_or_before", "between", "is_null", "not_null"];
const OPS_BY_CATEGORY = {
  string: STRING_OPS,
  unknown: STRING_OPS,
  number: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"],
  date: DATE_OPS,
  timestamp: DATE_OPS,
  boolean: ["is_true", "is_false", "is_null", "not_null"],
  array: ["has_value", "is_empty", "not_empty", "is_null", "not_null"],
  struct: ["is_null", "not_null"],
};

// The <input type> for a clause's value box, from the category of the
// literal the operator takes.
function inputFor(category, op) {
  if (op === "on") return "date";
  switch (category) {
    case "number":
      return "number";
    case "date":
      return "date";
    case "timestamp":
      return "datetime-local";
    default:
      return "text";
  }
}

// The category the VALUE is rendered as (differs from the column's for
// arrays — the value is an element — and for "is on" — always a date).
function valueCategory(clause) {
  if (clause.op === "on") return "date";
  if (clause.category === "array") return "string";
  return clause.category || "unknown";
}

export function operatorsFor(category, glueType = null) {
  const ids = OPS_BY_CATEGORY[category] || OPS_BY_CATEGORY.unknown;
  return ids
    // array_contains on a struct element needs a struct literal — custom SQL
    .filter((op) => op !== "has_value" || !glueType || arrayElementCategory(glueType) === "string")
    .map((op) => ({ op, ...OPS[op], input: OPS[op].arity ? inputFor(category, op) : "none" }));
}

// ------------------------------------------------------------------ clauses

export function emptyClause(kind, ctx = {}) {
  const base = { id: newClauseId(), kind };
  if (kind === "list") {
    const detected = ctx.detected ?? [];
    const idKind = detected.some((d) => d.idKind === "ppid")
      ? "ppid"
      : detected.some((d) => d.idKind === "isbn13")
        ? "isbn13"
        : "other";
    return {
      ...base,
      idKind,
      column: suggestListColumn(idKind, detected, ctx.primaryAlias),
      values: [],
      invalid: [],
      suspicious: [],
    };
  }
  // op "eq" is the placeholder for a clause with no column yet; the real
  // default is chosen when a column is picked (attributeClauseForColumn)
  if (kind === "attribute") return { ...base, column: "", category: "unknown", op: "eq", value: "", value2: "" };
  return { ...base, sql: "" };
}

// The operator a clause keeps when its category changes: the author's own
// when the new category has it; else "is between" for a date or timestamp
// (owner decision 2026-09-22 — a date attribute is a from/to range, not a
// list of days to pick from); else the category's first operator.
function defaultOpFor(category, glueType, op) {
  const ops = operatorsFor(category, glueType);
  if (ops.some((o) => o.op === op)) return op;
  return category === "date" || category === "timestamp" ? "between" : ops[0].op;
}

// The attribute clause after the author picks `column` (FilterStep's column
// change): category from the live Glue type (unknown when there is none),
// operator per defaultOpFor, values cleared — a value typed for the previous
// column is meaningless for this one.
export function attributeClauseForColumn(clause, column, ctx) {
  const glueType = columnGlueType(column, ctx);
  const category = glueTypeCategory(glueType);
  return { ...clause, column, category, op: defaultOpFor(category, glueType, clause?.op), value: "", value2: "" };
}

export function customClause(sql) {
  return { id: newClauseId(), kind: "custom", sql: String(sql ?? "") };
}

function compileAttribute(c, col) {
  const cat = valueCategory(c);
  const lit = (v) => renderLiteral(cat, v) ?? "{value}";
  const like = (v, wrap) => (String(v ?? "").trim() ? likeLiteral(String(v).trim(), wrap) : "{value}");
  switch (c.op) {
    case "eq":
      return `${col} = ${lit(c.value)}`;
    case "neq":
      return `${col} <> ${lit(c.value)}`;
    case "contains":
      return `${col} LIKE ${like(c.value, { prefix: "%", suffix: "%" })}`;
    case "starts_with":
      return `${col} LIKE ${like(c.value, { suffix: "%" })}`;
    case "ends_with":
      return `${col} LIKE ${like(c.value, { prefix: "%" })}`;
    case "gt":
    case "after":
      return `${col} > ${lit(c.value)}`;
    case "gte":
    case "on_or_after":
      return `${col} >= ${lit(c.value)}`;
    case "lt":
    case "before":
      return `${col} < ${lit(c.value)}`;
    case "lte":
    case "on_or_before":
      return `${col} <= ${lit(c.value)}`;
    case "between":
      return `${col} BETWEEN ${lit(c.value)} AND ${lit(c.value2)}`;
    case "on":
      return c.category === "timestamp" ? `to_date(${col}) = ${lit(c.value)}` : `${col} = ${lit(c.value)}`;
    case "is_true":
      return `${col} = true`;
    case "is_false":
      return `${col} = false`;
    case "has_value":
      return `array_contains(${col}, ${lit(c.value)})`;
    case "is_empty":
      // size(NULL) is -1 in Spark, so the null guard is what makes "empty" honest
      return `(${col} IS NULL OR size(${col}) = 0)`;
    case "not_empty":
      return `size(${col}) > 0`;
    case "is_null":
      return `${col} IS NULL`;
    case "not_null":
      return `${col} IS NOT NULL`;
    default:
      return `${col} {operator}`;
  }
}

// Always renders — a half-built clause shows {placeholders} (the product
// domain's branchWhere precedent), so the preview survives every keystroke.
// maxListPreview elides a long identifier list for the on-screen copy only.
export function compileClause(clause, { maxListPreview = Infinity } = {}) {
  if (!clause) return "";
  if (clause.kind === "custom") return String(clause.sql ?? "").trim();
  const col = clause.column || "{column}";
  if (clause.kind === "list") {
    const values = clause.values || [];
    if (!values.length) return `${col} IN ({values})`;
    const shown = values.slice(0, maxListPreview).map(escapeLiteral);
    const more = values.length - shown.length;
    return `${col} IN (${shown.join(", ")}${more > 0 ? `, … /* +${fmt(more)} more */` : ""})`;
  }
  if (clause.kind === "attribute") return compileAttribute(clause, col);
  return "";
}

export function isComplete(clause) {
  if (!clause) return false;
  if (clause.kind === "custom") return String(clause.sql ?? "").trim() !== "";
  if (!clause.column) return false;
  if (clause.kind === "list") {
    const n = clause.values?.length ?? 0;
    return n >= 1 && n <= LIST_CAP;
  }
  if (clause.kind === "attribute") {
    const meta = operatorsFor(clause.category || "unknown").find((o) => o.op === clause.op);
    if (!meta) return false;
    const cat = valueCategory(clause);
    if (meta.arity >= 1 && renderLiteral(cat, clause.value) == null) return false;
    if (meta.arity >= 2 && renderLiteral(cat, clause.value2) == null) return false;
    return true;
  }
  return false;
}

// Order preserved: index i here === engine filters[i], which is how the
// engine check's `filters[i]` issues find their clause again.
export function completeClauses(clauses) {
  return (clauses || []).filter(isComplete);
}

export function compileFilters(clauses) {
  return completeClauses(clauses).map((c) => compileClause(c));
}

// ------------------------------------------------------------------ context

// What the clause editors and checks need to know about the feed, derived
// once from feedSources + the live columns result (types are the additive
// `types` map the columns route returns for live iceberg sources — null when
// absent, which every consumer treats as "unknown").
export function filterContext(feedSources = [], attrsBySource = {}) {
  const aliases = feedSources.map((e) => e.alias);
  const columnsByAlias = {};
  const typesByAlias = {};
  const descriptionsByAlias = {}; // UIFPE-562: the Gold master's per-attribute text, or null
  const joinTypeByAlias = {};
  const sourceIdByAlias = {};
  const groupOfAlias = {};       // UIFPE-592: element alias -> the table alias it is shown under
  const groupLabelByAlias = {};  // UIFPE-592: table alias -> the picker's group heading
  feedSources.forEach((e, i) => {
    columnsByAlias[e.alias] = knownColumns(e.source, attrsBySource);
    typesByAlias[e.alias] = attrsBySource?.[e.source.id]?.types ?? null;
    descriptionsByAlias[e.alias] = attrsBySource?.[e.source.id]?.descriptions ?? null;
    joinTypeByAlias[e.alias] = i === 0 ? null : e.join?.type || "left";
    sourceIdByAlias[e.alias] = e.source.id;
    groupOfAlias[e.alias] = e.alias;
    groupLabelByAlias[e.alias] = e.join?.relationship ? tableHeading(e) : `${e.alias} · ${e.source.id}`;
  });
  // UIFPE-581/592: a spread list's element is addressed like a source, but
  // shown under its table: only the fields the table does not already have
  // (listColumnsFor). Fields still unknown (types loading) keep the alias
  // with no columns, like a source whose introspection is still loading; a
  // list that adds nothing (program's list of ids) is not offered at all.
  const elementAliases = [];
  for (const el of elementAliasesOf(feedSources)) {
    if (aliases.includes(el.alias)) continue;
    const fields = listColumnsFor(el, feedSources, attrsBySource);
    if (fields !== null && fields.length === 0) continue;
    aliases.push(el.alias);
    elementAliases.push(el.alias);
    columnsByAlias[el.alias] = (fields || []).map((f) => f.name);
    typesByAlias[el.alias] = fields ? Object.fromEntries(fields.map((f) => [f.name, f.type])) : null;
    descriptionsByAlias[el.alias] = null;
    joinTypeByAlias[el.alias] = null;
    sourceIdByAlias[el.alias] = el.summary;
    groupOfAlias[el.alias] = el.tableAlias;
  }
  return { aliases, primaryAlias: aliases[0] ?? null, columnsByAlias, typesByAlias, descriptionsByAlias,
           joinTypeByAlias, sourceIdByAlias, elementAliases, groupOfAlias, groupLabelByAlias,
           // UIFPE-607: what a row is and how to keep fewer, for the Conditions intro
           rowNote: [rowShape(feedSources), narrowHint(feedSources)].filter(Boolean).join(" ") };
}

// The Glue type of an `alias.col` reference, case-insensitive on the column
// (Glue lowercases; the author may have typed the true case), or null.
export function columnGlueType(column, ctx) {
  const [alias, name] = splitRef(column);
  const types = ctx?.typesByAlias?.[alias];
  if (!types || !name) return null;
  if (types[name] != null) return types[name];
  const key = Object.keys(types).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? types[key] : null;
}

// ------------------------------------------------------------------- checks

// Every `alias.` qualifier a custom condition mentions that is NOT a feed
// alias. Only the FIRST segment of a dotted chain counts, so
// price.priceSpecifications.currency yields nothing when price is a source.
// Completed string literals are blanked first (backslash-escaped quotes
// respected), so 'a.b' is never read as alias a.
export function unknownQualifiers(sql, aliases = []) {
  const text = String(sql || "").replace(/'(?:[^'\\]|\\.)*'/g, "''");
  const out = [];
  for (const m of text.matchAll(/(?<![.\w])([A-Za-z_]\w*)\s*\.\s*[A-Za-z_]/g)) {
    if (!aliases.includes(m[1]) && !out.includes(m[1])) out.push(m[1]);
  }
  return out;
}

// Bare tokens in a custom condition that name a known column without an
// alias — the engine's reference check skips them, so they would fail at run
// time. A token followed by "(" is a function, by "." an alias; one preceded
// by "." is already qualified. Returns [{ alias, column }] so the message can
// say exactly what to write.
export function unqualifiedColumns(sql, columnsByAlias = {}) {
  const known = new Map();
  for (const [alias, cols] of Object.entries(columnsByAlias)) {
    for (const c of cols || []) {
      const k = c.toLowerCase();
      if (!known.has(k)) known.set(k, { alias, column: c });
    }
  }
  if (!known.size) return [];
  const text = String(sql || "").replace(/'(?:[^'\\]|\\.)*'/g, "''");
  const out = [];
  const re = /[A-Za-z_]\w*/g;
  let m;
  while ((m = re.exec(text))) {
    const before = text.slice(0, m.index).trimEnd().slice(-1);
    const after = text.slice(m.index + m[0].length).trimStart()[0];
    if (before === "." || after === "." || after === "(") continue;
    const hit = known.get(m[0].toLowerCase());
    if (hit && !out.some((o) => o.column === hit.column)) out.push(hit);
  }
  return out;
}

const idNoun = (idKind, n) => {
  const base = idKind === "ppid" ? "PPID" : idKind === "isbn13" ? "ISBN-13" : "value";
  return n === 1 ? base : `${base}s`;
};

// Problems with ONE clause. level "block" stops Next (incomplete, over the
// cap, references a source no longer in the feed); "warn" is advice (ignored
// invalid identifiers, a suspicious PPID, a bare column in custom SQL).
export function clauseIssues(clause, ctx) {
  const issues = [];
  const seen = new Set();
  const add = (level, message) => {
    if (seen.has(message)) return;
    seen.add(message);
    issues.push({ level, message });
  };
  const aliases = ctx?.aliases ?? [];

  if (clause.kind === "custom") {
    if (!String(clause.sql ?? "").trim()) {
      add("block", "Write a Spark SQL condition, or remove this row");
      return issues;
    }
    const unknown = unknownQualifiers(clause.sql, aliases);
    if (unknown.length) {
      add(
        "warn",
        `Mentions «${unknown.join("», «")}», which is not a source alias (aliases: ${aliases.join(", ") || "none"})`
      );
    }
    const bare = unqualifiedColumns(clause.sql, ctx?.columnsByAlias ?? {});
    if (bare.length) {
      add(
        "warn",
        `«${bare[0].column}» is not alias-qualified — write ${bare[0].alias}.${bare[0].column}; the engine does not check bare names`
      );
    }
    return issues;
  }

  if (!clause.column) {
    add("block", clause.kind === "list" ? "Choose the identifier column" : "Choose a column");
  } else {
    const [alias, name] = splitRef(clause.column);
    if (!aliases.includes(alias)) {
      add("block", `Source «${alias}» is no longer in the feed — remove this condition or re-add the source`);
    } else {
      const cols = ctx?.columnsByAlias?.[alias] || [];
      if (cols.length && !cols.some((c) => c.toLowerCase() === name.toLowerCase())) {
        add("warn", `«${name}» is not in the live column list for ${alias}`);
      }
    }
  }

  if (clause.kind === "list") {
    const n = clause.values?.length ?? 0;
    if (n === 0) add("block", "Paste or upload at least one identifier");
    else if (n > LIST_CAP) {
      add(
        "block",
        `${fmt(LIST_CAP)} identifiers is the limit for one condition (you have ${fmt(n)}) — trim the list; a second condition would be ANDed, not combined`
      );
    }
    const bad = clause.invalid?.length ?? 0;
    if (bad) add("warn", `${fmt(bad)} invalid ${idNoun(clause.idKind, bad)} ignored — fix or remove ${bad === 1 ? "it" : "them"}`);
    const odd = clause.suspicious?.length ?? 0;
    if (odd) {
      add(
        "warn",
        `${fmt(odd)} ${idNoun(clause.idKind, odd)} ${odd === 1 ? "looks" : "look"} unusual (${clause.suspicious[0].reason}) — kept and matched exactly`
      );
    }
    return issues;
  }

  // attribute
  const meta = operatorsFor(clause.category || "unknown").find((o) => o.op === clause.op);
  if (!meta) {
    add("block", "Choose how to compare");
    return issues;
  }
  const cat = valueCategory(clause);
  const check = (v) => {
    if (String(v ?? "").trim() === "") {
      add("block", meta.arity === 2 ? "Enter both values" : "Enter a value — to match blanks use “is null”");
    } else if (renderLiteral(cat, v) == null) {
      add(
        "block",
        cat === "number"
          ? "The value must be a number"
          : cat === "date"
            ? "The value must be a date (YYYY-MM-DD)"
            : "The value must be a date and time (YYYY-MM-DD HH:MM)"
      );
    }
  };
  if (meta.arity >= 1) check(clause.value);
  if (meta.arity >= 2) check(clause.value2);
  return issues;
}

// Problems with the SET of clauses (the store's size cap).
export function filterSetIssues(clauses) {
  const bytes = utf8Bytes(JSON.stringify({ version: 1, clauses: clauses || [] }));
  if (bytes > FILTER_BYTES_CAP) {
    return [
      {
        level: "block",
        message: `The filter is too large to store (${fmt(Math.round(bytes / 1024))} KB of ${fmt(FILTER_BYTES_CAP / 1000)} KB) — shorten the identifier lists`,
      },
    ];
  }
  return [];
}

export function filterIssues(clauses, ctx) {
  const byId = {};
  let blocking = false;
  for (const c of clauses || []) {
    const issues = clauseIssues(c, ctx);
    byId[c.id] = issues;
    if (issues.some((i) => i.level === "block")) blocking = true;
  }
  const set = filterSetIssues(clauses);
  if (set.some((i) => i.level === "block")) blocking = true;
  return { byId, set, blocking };
}

// The Filter step's Next gate: no blocking issue. Zero clauses passes —
// "every row" is a legitimate (surfaced) choice.
export function filtersComplete(clauses, ctx) {
  return !filterIssues(clauses, ctx).blocking;
}

// A hint, not an issue: filtering on a left-joined source drops driver rows
// that have no match there — often intended, always worth saying.
export function leftJoinHint(clause, ctx) {
  if (!clause?.column || clause.kind === "custom") return null;
  const [alias] = splitRef(clause.column);
  if (!alias || alias === ctx?.primaryAlias) return null;
  if (ctx?.joinTypeByAlias?.[alias] !== "left") return null;
  return `${alias} is left-joined: ${ctx.primaryAlias} rows with no ${alias} match are excluded by this condition`;
}

// Once live types land, an attribute clause authored as "unknown" moves to
// its real category — but only when the operator the author chose exists
// there (a "contains" on a numeric column stays as written), except that a
// date or timestamp column always upgrades and takes "is between" when the
// author's operator is not a date operator (owner decision 2026-09-22; the
// value stays, so the clause asks for its second date rather than running
// a string comparison against a date). Returns the same array reference
// when nothing changed (the wizard's effect idiom).
export function upgradeClauseCategories(clauses, ctx) {
  let changed = false;
  const next = (clauses || []).map((c) => {
    if (c.kind !== "attribute" || (c.category || "unknown") !== "unknown" || !c.column) return c;
    const glueType = columnGlueType(c.column, ctx);
    if (!glueType) return c;
    const category = glueTypeCategory(glueType);
    if (category === "unknown") return c;
    const hasOp = operatorsFor(category, glueType).some((o) => o.op === c.op);
    if (!hasOp && category !== "date" && category !== "timestamp") return c;
    changed = true;
    return hasOp ? { ...c, category } : { ...c, category, op: "between" };
  });
  return changed ? next : clauses;
}

// -------------------------------------------------------------- identifiers

// product_id and inventory_item_id are how a Gold export spells its
// identifier headers (owner decision 2026-09-22)
const HEADER_WORDS = new Set([
  "ppid", "ppids", "product_id", "inventory_item_id", "isbn", "isbns", "isbn13", "isbn-13", "isbn_13", "ean", "id", "ids",
  "identifier", "identifiers",
]);

export function isbn13CheckDigitOk(digits) {
  if (!/^\d{13}$/.test(digits)) return false;
  let sum = 0;
  for (let i = 0; i < 12; i += 1) sum += Number(digits[i]) * (i % 2 === 0 ? 1 : 3);
  return (10 - (sum % 10)) % 10 === Number(digits[12]);
}

function classifyIdentifier(token, idKind) {
  if (idKind === "isbn13") {
    const v = token.replace(/[-\s]/g, "");
    if (/^\d{9}[\dXx]$/.test(v)) return { reason: "looks like an ISBN-10 — convert it to ISBN-13" };
    if (!/^\d{13}$/.test(v)) return { reason: "not 13 digits" };
    if (!/^97[89]/.test(v)) return { reason: "must start 978 or 979" };
    if (!isbn13CheckDigitOk(v)) return { reason: "check digit mismatch" };
    return { value: v };
  }
  if (idKind === "ppid") {
    if (/['"\\()]/.test(token)) return { reason: "unexpected character" };
    return { value: token, unusual: /^A/.test(token) ? null : "does not start with 'A'" };
  }
  return { value: token };
}

// Pasted or uploaded text → clean identifiers. Tokens split on commas,
// semicolons and any whitespace; surrounding quotes and a BOM stripped; a
// leading header word (ppid, isbn13, id …) skipped for the two known kinds.
// ISBN-13s are canonicalised (hyphens/spaces removed) and check-digit
// verified — failures are rejected, since the digit is a hard fact; PPIDs
// not starting with A are kept but reported as suspicious, since rejecting
// a legitimate export is worse than one value matching nothing. Dedupe is
// exact and case-sensitive (Spark matches that way); first occurrence wins.
export function normalizeIdentifiers(text, idKind = "other") {
  const raw = String(text ?? "").replace(/^\uFEFF/, "");
  let tokens = raw
    .split(/[\s,;]+/)
    .map((t) => t.replace(/^["'`]+|["'`]+$/g, ""))
    .filter(Boolean);
  let headerSkipped = false;
  if (tokens.length && idKind !== "other" && HEADER_WORDS.has(tokens[0].toLowerCase())) {
    tokens = tokens.slice(1);
    headerSkipped = true;
  }
  const seen = new Set();
  const values = [];
  const invalid = [];
  const suspicious = [];
  let duplicates = 0;
  for (const token of tokens) {
    const { value, reason, unusual } = classifyIdentifier(token, idKind);
    if (reason) {
      invalid.push({ value: token, reason });
      continue;
    }
    if (seen.has(value)) {
      duplicates += 1;
      continue;
    }
    seen.add(value);
    values.push(value);
    if (unusual) suspicious.push({ value, reason: unusual });
  }
  return { values, duplicates, invalid, suspicious, headerSkipped };
}

const ID_COLUMN_PATTERNS = [
  { re: /^ppid$/i, idKind: "ppid", label: "PPID" },
  // the PPID is physically product_id — no Gold table has a column named
  // ppid (owner decision 2026-09-22); parent_product_id is not the PPID
  { re: /^product_id$/i, idKind: "ppid", label: "PPID" },
  { re: /^(isbn[-_]?13|isbn)$/i, idKind: "isbn13", label: "ISBN-13" },
  { re: /^isbn[-_]?10$/i, idKind: "other", label: "ISBN-10" },
];

// Identifier-shaped columns across the feed, driving source first, names
// exactly as the source lists them (never re-cased).
export function detectIdentifierColumns(feedSources = [], attrsBySource = {}) {
  const out = [];
  for (const entry of feedSources) {
    for (const column of knownColumns(entry.source, attrsBySource)) {
      const hit = ID_COLUMN_PATTERNS.find((p) => p.re.test(column));
      if (hit) out.push({ alias: entry.alias, column, idKind: hit.idKind, label: hit.label });
    }
  }
  return out;
}

// The column a new list clause starts on: the driving source's match (ppid
// is on product, price AND stock — under a left join the driver is the one
// to filter), else the first match anywhere, else nothing chosen.
export function suggestListColumn(idKind, detected = [], primaryAlias = null) {
  const matches = detected.filter((d) => d.idKind === idKind);
  const pick = matches.find((d) => d.alias === primaryAlias) ?? matches[0];
  return pick ? `${pick.alias}.${pick.column}` : "";
}

// ------------------------------------------------------------ plain English

export function describeClause(clause) {
  if (!clause) return "";
  if (clause.kind === "custom") return `Spark SQL: ${String(clause.sql ?? "").trim() || "(empty)"}`;
  const col = clause.column || "(column)";
  if (clause.kind === "list") {
    const n = clause.values?.length ?? 0;
    if (n === 0) return `${col} is one of … (no identifiers yet)`;
    if (n <= 3) return `${col} is one of ${clause.values.join(", ")}`;
    return `${col} is one of ${fmt(n)} ${idNoun(clause.idKind, n)}`;
  }
  const meta = OPS[clause.op];
  if (!meta) return `${col} … (incomplete)`;
  const quote = (x) => (["number", "date", "timestamp"].includes(valueCategory(clause)) ? x : `'${x}'`);
  const v = String(clause.value ?? "").trim();
  const v2 = String(clause.value2 ?? "").trim();
  if (meta.arity === 0) return `${col} ${meta.label}`;
  if (meta.arity === 1) return v ? `${col} ${meta.label} ${quote(v)}` : `${col} ${meta.label} … (incomplete)`;
  return v && v2 ? `${col} ${meta.label} ${quote(v)} and ${quote(v2)}` : `${col} ${meta.label} … (incomplete)`;
}

// One line per COMPLETE clause — the Review step's summary.
export function describeFilters(clauses) {
  return completeClauses(clauses).map(describeClause);
}

// UIFPE-717: a filter as one compact row on the Rules step — an automatic
// label (owner decision 2026-09-30: no typed names, nothing new in the
// config) over the Spark SQL it compiles to
export function filterRuleLabel(clause, index) {
  const what = clause?.kind === "custom" ? "custom Spark SQL" : describeClause(clause);
  return `Filter ${index + 1} · ${what}`;
}

export function filterRuleDefinition(clause) {
  return isComplete(clause) ? compileClause(clause, { maxListPreview: 5 }) : "incomplete — not applied until it is finished";
}

// --------------------------------------------------------------- round-trip

function sanitizeClauses(raw) {
  const out = [];
  for (const c of raw) {
    if (!c || typeof c !== "object") continue;
    const id = c.id || newClauseId();
    if (c.kind === "list") {
      out.push({
        id,
        kind: "list",
        column: String(c.column ?? ""),
        idKind: ID_KINDS.some((k) => k.id === c.idKind) ? c.idKind : "other",
        values: Array.isArray(c.values) ? c.values.map(String) : [],
        invalid: Array.isArray(c.invalid) ? c.invalid : [],
        suspicious: Array.isArray(c.suspicious) ? c.suspicious : [],
      });
    } else if (c.kind === "attribute") {
      out.push({
        id,
        kind: "attribute",
        column: String(c.column ?? ""),
        category: CATEGORIES.includes(c.category) ? c.category : "unknown",
        op: String(c.op ?? ""),
        value: String(c.value ?? ""),
        value2: String(c.value2 ?? ""),
      });
    } else if (c.kind === "custom") {
      out.push({ id, kind: "custom", sql: String(c.sql ?? "") });
    }
  }
  return out;
}

const sameList = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

// The clauses to edit for a stored version. The saved model is used ONLY when
// it recompiles to exactly the published `filters` — the prefill must never
// show structure the engine did not run. Otherwise EVERY filters[i] becomes a
// custom clause (the old prefill kept filters[0] alone and silently narrowed
// a republished export), then the pre-builder settings.row_filter, then [].
export function filtersFromConfig(mappingConfig, settings) {
  const filters = (mappingConfig?.filters ?? []).map((f) => String(f ?? "").trim()).filter(Boolean);
  const model = settings?.filter_model;
  if (model && Array.isArray(model.clauses)) {
    const clauses = sanitizeClauses(model.clauses);
    if (clauses.length === model.clauses.length && sameList(compileFilters(clauses), filters)) {
      return { clauses, provenance: "model" };
    }
  }
  if (filters.length) return { clauses: filters.map(customClause), provenance: "filters" };
  const legacy = String(settings?.row_filter ?? "").trim();
  if (legacy) return { clauses: [customClause(legacy)], provenance: "legacy" };
  return { clauses: [], provenance: "none" };
}

// ------------------------------------------------ renamed columns (UIFPE-568)
// Gold columns went camelCase → snake_case on 2026-09-12 (N8). A config
// authored before that says `product.itemType`; the live table says
// `item_type`. Spark resolves case-insensitively only, so the reference stays
// unresolvable at run time — the wizard therefore REWRITES it to the live
// name the moment live columns land, and says so, rather than asking the
// validator to wave it through. A name that already resolves (any case) is
// left alone; a name matching two live columns underscore-insensitively is
// left alone too (the validator reports it).

const canonicalName = (name) => String(name ?? "").replace(/_/g, "").toLowerCase();

// The live `alias.column` a stale reference should become, or null.
export function staleRename(ref, columnsByAlias) {
  const [alias, name] = splitRef(ref);
  const cols = columnsByAlias?.[alias];
  if (!alias || !name || !cols?.length) return null;
  if (cols.some((c) => c.toLowerCase() === name.toLowerCase())) return null;
  const hits = cols.filter((c) => canonicalName(c) === canonicalName(name));
  return hits.length === 1 ? `${alias}.${hits[0]}` : null;
}

// Every `alias.name` token in a Spark SQL text (outside string literals)
// that staleRename would rewrite, rewritten. `onRename(from, to)` is told
// about each distinct rewrite.
export function renameStaleReferencesInSql(sql, columnsByAlias, onRename = () => {}) {
  const text = String(sql ?? "");
  const aliases = Object.keys(columnsByAlias || {}).filter((a) => (columnsByAlias[a] || []).length);
  if (!text || !aliases.length) return text;
  const literals = [];
  for (const m of text.matchAll(/'(?:[^'\\]|\\.)*'/g)) literals.push([m.index, m.index + m[0].length]);
  const inLiteral = (i) => literals.some(([a, b]) => i >= a && i < b);
  const re = new RegExp(`(?<![.\\w])(${aliases.join("|")})\\.([A-Za-z_]\\w*)`, "g");
  return text.replace(re, (whole, alias, name, offset) => {
    if (inLiteral(offset)) return whole;
    const to = staleRename(`${alias}.${name}`, columnsByAlias);
    if (!to) return whole;
    onRename(`${alias}.${name}`, to);
    return to;
  });
}

// Clauses (attribute/list columns, custom SQL) and mappings (source, sources,
// expression, expressions) with their stale references rewritten. Returns the
// SAME objects when nothing changed, so a setState from it never loops;
// `renamed` lists each distinct {from, to} once.
export function renameStaleReferences({ clauses = [], mappings = [] }, columnsByAlias) {
  const renamed = [];
  const note = (from, to) => {
    if (!renamed.some((r) => r.from === from)) renamed.push({ from, to });
  };
  const sql = (s) => renameStaleReferencesInSql(s, columnsByAlias, note);
  let changed = false;

  const nextClauses = clauses.map((c) => {
    if (c.kind === "custom") {
      const s = sql(c.sql);
      if (s === String(c.sql ?? "")) return c;
      changed = true;
      return { ...c, sql: s };
    }
    const to = staleRename(c.column, columnsByAlias);
    if (!to) return c;
    note(c.column, to);
    changed = true;
    return { ...c, column: to };
  });

  const nextMappings = mappings.map((m) => {
    const patch = {};
    if (m.source) {
      const to = staleRename(m.source, columnsByAlias);
      if (to) {
        note(m.source, to);
        patch.source = to;
      }
    }
    if (Array.isArray(m.sources)) {
      const sources = m.sources.map((s) => {
        const to = staleRename(s, columnsByAlias);
        if (to) note(s, to);
        return to || s;
      });
      if (sources.some((s, i) => s !== m.sources[i])) patch.sources = sources;
    }
    if (m.expression) {
      const e = sql(m.expression);
      if (e !== m.expression) patch.expression = e;
    }
    if (Array.isArray(m.expressions)) {
      const expressions = m.expressions.map(sql);
      if (expressions.some((e, i) => e !== m.expressions[i])) patch.expressions = expressions;
    }
    if (!Object.keys(patch).length) return m;
    changed = true;
    return { ...m, ...patch };
  });

  return changed ? { clauses: nextClauses, mappings: nextMappings, renamed } : { clauses, mappings, renamed: [] };
}

// ------------------------------------------------------------- engine check

// The config sent to POST /validate/mapping-config by "Check filter": the
// real sources and joins, the compiled filters, and the stub mapping.
export function filterCheckConfig(feedSources, filters) {
  return buildMappingConfig({ feedSources, mappings: [FILTER_CHECK_STUB_MAPPING], filters });
}

// Route the validator's issues: filters[i] → the i-th COMPLETE clause;
// api/engine/config → shown once; anything else (sources, joins) is counted
// and left for the Review step, where it belongs.
export function filterCheckIssues(issues = []) {
  const byIndex = {};
  const general = [];
  let otherCount = 0;
  for (const issue of issues) {
    const m = /^filters\[(\d+)\]/.exec(issue.where || "");
    if (m) {
      const i = Number(m[1]);
      if (!byIndex[i]) byIndex[i] = [];
      byIndex[i].push(issue.message);
    } else if (["api", "engine", "config"].includes(issue.where)) {
      general.push(issue.message);
    } else {
      otherCount += 1;
    }
  }
  return { byIndex, general, otherCount };
}

// ------------------------------------------------------- live data (UIFPE-441)
// Value suggestions, the cardinality profile (asynchronous and persisted since
// UIFPE-514, started when a source is chosen) and the matched-rows preview
// read Gold through Athena. The rules here decide what the UI asks for and
// how it reads the answer; the requests themselves live in Wizard.jsx /
// FilterStep.jsx.

export const PREVIEW_POLL_MS = 2000;
export const PREVIEW_UI_CAP_S = 120;

// A text column with more distinct values than this is never offered by the
// attribute picker and never gets value suggestions — descriptions, URLs,
// owner names, version strings: a search, not a choice. Mirrors preview.py's
// TEXT_CARDINALITY_CAP (owner decision 2026-09-22).
export const TEXT_CARDINALITY_CAP = 1000;

// Identifiers by NAME — whole name, case-insensitive — are never offered as
// an attribute and never suggested: an equals on one is an identifier list
// in disguise. Widened at UIFPE-514 (owner decision 2026-09-22) from
// ppid/isbn* to the identifier columns as Gold actually spells them: the
// PPID is physically `product_id` (no Gold table has a column named ppid);
// ppid, isbn, isbn_13 and isbn_10 stay for history. preview.py keeps the OLD
// pattern as SAMPLE_IDENTIFIER_RE for the preview sample's projection, which
// must not change — this is the picker's rule, not the sample's.
export const IDENTIFIER_COLUMN_RE =
  /^(ppid|product_id|parent_product_id|inventory_item_id|source_item_number|program_id|content_id|isbn13|isbn_13|isbn10|isbn_10|isbn)$/i;

// Text is what needs a value list: Glue string/varchar/char, and a column
// whose type the wizard does not know (no live type map) — treated as text
// because that is the operator family it gets.
const TEXT_CATEGORIES = new Set(["string", "unknown"]);

// A source's cardinality profile as the wizard stores it, BY ALIAS:
//   undefined / null                 not requested yet (held back like pending)
//   { status: "pending" }            POST accepted, GET polling
//   { status: "done", rows, columns: { col: { distinct, unique_like, identifier } } }
//   { status: "failed", reason }     also "unavailable" — the reason is shown
// The pre-514 shape ({ columns } with no status) reads as done so a profile
// cached mid-migration keeps working. `unique_like` stays in the payload for
// compatibility but no rule here reads it (owner decision 2026-09-22): it
// measured distinct ≥ 0.5 × rows on an item × organisation grain, where a
// genuinely low-cardinality attribute looked unique, and its denominator
// counted rows whose value was NULL.
function profileState(profile) {
  if (!profile) return { status: "pending", columns: {}, reason: null };
  const status = profile.status ?? (profile.columns ? "done" : "pending");
  if (status === "done") return { status: "done", columns: profile.columns || {}, reason: null };
  if (status === "pending" || status === "running" || status === "absent") return { status: "pending", columns: {}, reason: null };
  return {
    status: "failed",
    columns: {},
    reason: profile.reason || profile.error?.message || "the cardinality profile is unavailable",
  };
}

// The profile's entry for a column, case-insensitive like columnGlueType
// (Glue lowercases; the clause may carry the author's spelling).
function profileStat(columns, name) {
  if (!columns || !name) return null;
  if (columns[name]) return columns[name];
  const key = Object.keys(columns).find((k) => k.toLowerCase() === name.toLowerCase());
  return key ? columns[key] : null;
}

const overCap = (stat) => stat != null && Number(stat.distinct) > TEXT_CARDINALITY_CAP;

// Whether an attribute clause's value box should offer live suggestions,
// and when not, the reason worth telling the author (null = just no box to
// suggest into). Mirrors the API's own exclusions so the UI never asks for
// a list it knows it cannot get: identifiers by name, structured columns,
// and — once the profile is in — any column over TEXT_CARDINALITY_CAP.
// Without a profile the request proceeds as before (the API applies the
// same cap from its persisted copy when it has one).
export function suggestionsEligible(clause, ctx, profiles = {}) {
  const no = (reason = null) => ({ eligible: false, reason });
  if (!clause || clause.kind !== "attribute" || !clause.column) return no();
  const [alias, name] = splitRef(clause.column);
  if (!ctx?.aliases?.includes(alias) || !name) return no();
  const category = clause.category || "unknown";
  const meta = operatorsFor(category).find((o) => o.op === clause.op);
  if (!meta || meta.arity === 0) return no();
  if (IDENTIFIER_COLUMN_RE.test(name)) {
    return no("identifiers are unique per product — paste them as an identifier list instead");
  }
  if (category === "array" || category === "struct") return no("a structured column has no value list");
  if (category === "date" || category === "timestamp" || category === "boolean") return no();
  // UIFPE-581: values inside a spread list are not measured or suggested (yet)
  if (ctx.elementAliases?.includes(alias)) return no("values inside a list are not suggested — type the value");
  const profile = profileState(profiles?.[alias]);
  if (profile.status === "done" && overCap(profileStat(profile.columns, name))) {
    return no(`more than ${fmt(TEXT_CARDINALITY_CAP)} distinct values — type the value, or use Custom Spark SQL for a text search`);
  }
  return { eligible: true, reason: null };
}

// The body POST /preview/filter takes: the feed's config with the REAL
// mappings (UIFPE-603 — the sample is the export, ten rows of it; the stub
// only when nothing is mapped yet) and the compiled filters, plus the
// identifier lists spelled out — the server never re-parses an IN (...)
// string. Lists are the COMPLETE list clauses in order; the response's
// missing[] is positional to them.
export function previewRequest(feedSources, clauses, mappings = [], format = null) {
  return {
    mapping_config: buildMappingConfig({
      feedSources,
      mappings: mappings.length ? mappings : [FILTER_CHECK_STUB_MAPPING],
      filters: compileFilters(clauses),
    }),
    // UIFPE-609: the rendering format, so a bare list column in a CSV export
    // is refused here as the run would refuse it
    ...(format ? { format } : {}),
    lists: completeClauses(clauses)
      .filter((c) => c.kind === "list")
      .map((c) => ({ column: c.column, values: c.values })),
  };
}

export function previewSignature(request) {
  return JSON.stringify(request);
}

// Position of a list clause in previewRequest's lists[], or -1.
export function listIndexOf(clauses, clause) {
  return completeClauses(clauses)
    .filter((c) => c.kind === "list")
    .findIndex((c) => c.id === clause?.id);
}

// The headline over the sample: how many rows the filter keeps. The
// before-filter total and the percentage are NOT shown (owner decision
// 2026-09-21): a six-digit "of N rows (<0.01%)" beside a nine-row match said
// nothing the author asked, and for a joined feed "rows after the join" is
// not "products". The API still returns `total` — it comes from the same
// scan — and it still tells one thing worth saying: that the joined sources
// hold no rows at all, which is a different problem from a filter nothing
// matches.
export function describePreviewCounts(total, matched) {
  const t = Number(total) || 0;
  const m = Number(matched) || 0;
  if (t === 0 && m === 0) return "The joined sources hold no rows";
  if (m === 0) return "No rows match";
  return `${fmt(m)} row${m === 1 ? "" : "s"} match${m === 1 ? "es" : ""}`;
}

// The not-found entry for the i-th complete list clause, normalised, or null.
export function missingFor(result, listIndex) {
  const entry = result?.missing?.[listIndex];
  if (!entry) return null;
  return { column: entry.column ?? null, missing: entry.missing || [], truncated: Boolean(entry.truncated) };
}

// Which columns the ATTRIBUTE picker offers, per alias, and what it does with
// the rest (owner decisions 2026-09-22):
//   hidden[alias]      [{ column, reason }] — "identifier": by name, before the
//                      profile and whatever the type (they belong to an
//                      identifier list); "text": a text column the profile
//                      measured over TEXT_CARDINALITY_CAP
//   pending[alias]     text columns HELD BACK until the profile lands (none
//                      requested yet, or still running)
//   unmeasured[alias]  { reason } when the profile failed or is unavailable:
//                      its text columns are offered unmeasured, and the reason
//                      is for the picker to show — never a silent fallback
// Numbers, decimals, dates, timestamps and booleans are offered at once (a
// range or equality needs no value list); arrays and structs as before
// (contains / is empty). A text column with a done profile but no entry
// (not scalar, or added since) is offered. Free-text search on a hidden
// column stays reachable through Custom Spark SQL.
export function attributeColumnsFor(ctx, profiles = {}) {
  const offered = {};
  const hidden = {};
  const pending = {};
  const unmeasured = {};
  for (const alias of ctx?.aliases ?? []) {
    offered[alias] = [];
    hidden[alias] = [];
    pending[alias] = [];
    const profile = profileState(profiles?.[alias]);
    if (profile.status === "failed") unmeasured[alias] = { reason: profile.reason };
    // UIFPE-581: a spread list's fields have no cardinality profile — they
    // are offered as they are, text included, rather than held back forever
    const isElement = Boolean(ctx.elementAliases?.includes(alias));
    for (const column of ctx.columnsByAlias?.[alias] || []) {
      if (IDENTIFIER_COLUMN_RE.test(column)) {
        hidden[alias].push({ column, reason: "identifier" });
        continue;
      }
      const category = glueTypeCategory(columnGlueType(`${alias}.${column}`, ctx));
      if (!TEXT_CATEGORIES.has(category) || isElement) {
        offered[alias].push(column);
        continue;
      }
      if (profile.status === "pending") pending[alias].push(column);
      else if (profile.status === "done" && overCap(profileStat(profile.columns, column))) {
        hidden[alias].push({ column, reason: "text" });
      } else offered[alias].push(column);
    }
  }
  return { offered, hidden, pending, unmeasured };
}

// "~5 values" beside a column, "more than 1,000 values" over the cap, or ""
// when the profile is not done or never measured the column.
export function cardinalityLabel(profile, column) {
  const state = profileState(profile);
  if (state.status !== "done") return "";
  const stat = profileStat(state.columns, column);
  if (!stat || stat.distinct == null) return "";
  if (overCap(stat)) return `more than ${fmt(TEXT_CARDINALITY_CAP)} values`;
  return `~${fmt(stat.distinct)} value${stat.distinct === 1 ? "" : "s"}`;
}

// One footnote for the picker: what was hidden and where it went.
export function describeHidden(hidden) {
  const entries = Object.entries(hidden || {}).flatMap(([alias, list]) => (list || []).map((h) => ({ alias, ...h })));
  const ids = entries.filter((h) => h.reason === "identifier").map((h) => `${h.alias}.${h.column}`);
  const text = entries.filter((h) => h.reason === "text").map((h) => `${h.alias}.${h.column}`);
  const parts = [];
  if (ids.length) parts.push(`${ids.join(", ")} — identifiers: use an identifier list`);
  if (text.length) {
    parts.push(
      `${text.join(", ")} — more than ${fmt(TEXT_CARDINALITY_CAP)} values: use an identifier list to pick products, or Custom Spark SQL for a text search`
    );
  }
  return parts.length ? `Not offered here: ${parts.join(". ")}.` : "";
}

// The picker's note while text columns are held back: "measuring 4 more
// attributes…", or "" once every profile has landed.
export function describeMeasuring(pending) {
  const n = Object.values(pending || {}).reduce((sum, cols) => sum + (cols?.length ?? 0), 0);
  if (!n) return "";
  return `measuring ${fmt(n)} more attribute${n === 1 ? "" : "s"}…`;
}

export function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// ------------------------------------------ the matched rows as CSV (UIFPE-490)
// Beside a finished preview the author may take every matched row (in the
// preview's columns, capped) as one CSV — or carry on and schedule the feed.
// Nothing downloads by itself: the API answers a finished execution with a
// short-lived signed link, and these rules decide when the button is offered
// and what the link should say.

// mirrors the API's cap; the response's row_cap wins when present
export const DOWNLOAD_ROW_CAP = 100_000;

// Whether the download button may be offered for this preview result, and
// when not, the reason worth showing (null = nothing to say, no button).
export function canDownload(preview, stale) {
  const no = (reason = null) => ({ ok: false, reason });
  if (!preview || preview.status !== "done") return no();
  if (stale) return no("The filter changed — run Preview matches again before downloading.");
  if ((Number(preview.matched) || 0) === 0) return no("No rows match — there is nothing to download.");
  return { ok: true, reason: null };
}

// The download state that belongs to THIS preview result: same request
// signature, and the preview itself done and current. Anything else is
// null — the button offers a fresh download rather than a link to a file
// that answers a different filter.
export function downloadFor(download, preview, stale) {
  if (!download || !preview || preview.status !== "done" || stale) return null;
  return download.signature === preview.signature ? download : null;
}

// What the finished link says: how many rows the file carries, whether the
// cap cut it, and its size when the API could measure it.
export function describeDownload(matched, rowCap = DOWNLOAD_ROW_CAP, bytes = null) {
  const m = Number(matched) || 0;
  const cap = Number(rowCap) > 0 ? Number(rowCap) : DOWNLOAD_ROW_CAP;
  const rows = Math.min(m, cap);
  const truncated = m > cap;
  const size = bytes != null ? ` · ${fmtBytes(bytes)}` : "";
  const label = truncated
    ? `Download CSV — ${fmt(cap)} of ${fmt(m)} matched rows (capped)${size}`
    : `Download CSV — ${fmt(rows)} row${rows === 1 ? "" : "s"}${size}`;
  return { rows, truncated, label };
}
