// Structured branch conditions — the compiler contract for conditional
// columns. The original conversion left conditions as prose ("ELSE (<> 10002)",
// "10002 (Digital Catalog)") that a human or AI had to INTERPRET into SQL;
// this module makes them data a compiler RENDERS. The canonical string form
// stays intake-compatible, so registered entries parse and wizard output
// round-trips.
//
// Contract: named branches are equality tests on the discriminator (one value
// or an IN-list); at most one ELSE branch takes everything the named branches
// didn't. That is exactly the pattern product's mapping uses today
// (ITEM_CATALOG_GROUP_ID = 10002 vs everything else).

export interface BranchCondition {
  op: "equals" | "in" | "else";
  values: string[];       // empty for else
  label: string | null;   // human name, e.g. "Digital Catalog"
}

// "10002 (Digital Catalog)" | "10002,10003 (bundles)" | "ELSE" |
// legacy "ELSE (<> 10002)" — all parse; unknown prose degrades to a label-only
// equals so nothing is silently dropped.
export function parseCondition(raw: string): BranchCondition {
  const s = (raw ?? "").trim();
  if (/^else\b/i.test(s)) return { op: "else", values: [], label: null };
  const m = s.match(/^([\w.,\s-]+?)\s*(?:\((.*)\))?$/);
  const values = (m?.[1] ?? s).split(",").map((v) => v.trim()).filter(Boolean);
  const label = m?.[2]?.trim() || null;
  return { op: values.length > 1 ? "in" : "equals", values, label };
}

export function formatCondition(c: BranchCondition): string {
  if (c.op === "else") return "ELSE";
  const v = c.values.join(",");
  return c.label ? `${v} (${c.label})` : v;
}

// The deterministic SQL fragment for one branch's row filter. Half-built
// input (a branch whose value hasn't been typed yet) renders a {placeholder},
// never throws — the preview must survive every keystroke of authoring.
export function branchWhere(discColumn: string, c: BranchCondition,
                            namedSiblings: BranchCondition[]): string {
  if (c.op === "equals")
    return `${discColumn} = ${c.values.length ? lit(c.values[0]) : "{value}"}`;
  if (c.op === "in")
    return `${discColumn} IN (${c.values.length ? c.values.map(lit).join(", ") : "{values}"})`;
  // Proliferate branches can share one discriminator value and differ only
  // in scope — dedupe so the complement stays minimal.
  const named = [...new Set(namedSiblings.flatMap((s) => s.values))];
  if (!named.length) return `${discColumn} NOT IN ({other branches' values})`;
  return named.length === 1
    ? `${discColumn} <> ${lit(named[0])}`
    : `${discColumn} NOT IN (${named.map(lit).join(", ")})`;
}

// ── Proliferate row scopes — WHO receives a branch's value ─────────────────
// Renders the fan-out predicate for one RESOLVED scope (group references are
// resolved via resolveScope() first): the deterministic WHEN of the
// apply-time CASE (product: orgs.ORGANIZATION_CODE LIKE '01%').
import type { ResolvedScope } from "./types";

export function scopeWhere(s: ResolvedScope,
                           siblings: ResolvedScope[]): string {
  const col = s.column || "{column}";
  if (s.missing) return `/* row group "${s.label}" is not defined */`;
  // A complement group IS its base predicate, negated — never restated.
  if (s.not) return `NOT (${scopeWhere({ ...s, not: false, label: null }, [])})`;
  if (s.op === "equals")
    return `${col} = ${s.values.length ? lit(s.values[0]) : "{value}"}`;
  if (s.op === "in")
    return `${col} IN (${s.values.length ? s.values.map(lit).join(", ") : "{values}"})`;
  if (s.op === "startsWith")
    return s.values.length
      ? s.values.map((v) => `${col} LIKE '${v.replace(/'/g, "''")}%'`).join(" OR ")
      : `${col} LIKE '{prefix}%'`;
  // else = every row no sibling scope claimed
  const named = siblings.filter((x) => x.op !== "else" && !x.missing);
  if (!named.length) return `/* all rows — no sibling scopes */`;
  return `NOT (${named.map((x) => scopeWhere(x, [])).join(" OR ")})`;
}

// Human sentence for a scope — review summaries and branch captions.
export function scopeLabel(s: ResolvedScope): string {
  if (s.label) return s.label;
  if (s.op === "else") return "all remaining rows";
  if (s.op === "startsWith") return `${s.column} starting ${s.values.join("/")}`;
  return `${s.column} = ${s.values.join(", ")}`;
}

const lit = (v: string) => /^\d+(\.\d+)?$/.test(v) ? v : `'${v.replace(/'/g, "''")}'`;
