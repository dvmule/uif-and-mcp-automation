// Derived columns — the query is the contract (ADR-0008 / UIFPE-217).
//
// The mapping CARRIES the author's query, so there is no rendering to keep
// in sync and no drift gate: what this module provides is DISPLAY — the
// contract in words, and the wrap preview showing the query exactly as the
// compiler splices it (mirroring derivation_chain.py's render_wrap: CTE
// under the column's name, LEFT join on the declared pairs, _ID entity
// columns CAST AS BIGINT — the A3 rule). Validation (the ADR's battery) is
// the Python side's job at submit and in CI; nothing here judges the SQL.

import type { DerivationBlock } from "./types";

const cast = (col: string) =>
  col.toUpperCase().endsWith("_ID")
    ? `CAST(items.${col} AS BIGINT)` : `items.${col}`;

/** True when the block is the query contract this UI can display. A wizard
 *  draft saved before UIFPE-217 may still carry the retired step-chain shape
 *  (`{authoritative, steps}`) — callers show a re-import hint for those
 *  instead of crashing on the missing fields. */
export function isQueryBlock(b: unknown): b is DerivationBlock {
  return !!b && typeof b === "object"
    && typeof (b as DerivationBlock).sql === "string"
    && Array.isArray((b as DerivationBlock).keyedBy);
}

/** One line per fact of the declared contract, for the read-only display. */
export function contractLines(colName: string, block: DerivationBlock): string[] {
  const value = block.valueColumn ?? colName;
  const lines = [
    `one row per entity — ${(block.keyedBy ?? [])
      .map((k) => `${k.queryColumn} identifies it via ${k.entityColumn}`)
      .join("; ")}`,
    `value column: ${value}`,
  ];
  const reads = Object.entries(block.reads ?? {});
  if (reads.length) {
    lines.push("reads (computed from the query, never typed): "
      + reads.map(([t, cols]) => `${t} (${cols.length} columns)`).join(", "));
  }
  return lines;
}

/** The query as the compiler splices it — verbatim CTE + LEFT join. Mirrors
 *  derivation_chain.py's render_wrap, including the nested-kind COALESCE to
 *  array() (empty arrays are [] and never NULL): what the reviewer sees here
 *  must be the text the compiler writes. */
export function wrapPreview(colName: string, block: DerivationBlock,
                            kind?: string): string {
  const body = (block.sql ?? "").trim().split("\n")
    .map((l) => (l.trim() ? `  ${l}` : "")).join("\n");
  const pairs = (block.keyedBy ?? [])
    .map((k) => `${cast(k.entityColumn)} = d_${colName}.${k.queryColumn}`)
    .join(`\n${" ".repeat(19)}AND `);
  const value = block.valueColumn ?? colName;
  const select = kind === "nested"
    ? `COALESCE(d_${colName}.${value}, array())`
    : `d_${colName}.${value}`;
  return [
    `d_${colName} AS (  -- your query, spliced verbatim`,
    body,
    ")",
    `-- joined into the final SELECT:`,
    `--   LEFT JOIN d_${colName} ON ${pairs.split("\n").join("\n--   ")}`,
    `--   ${select} AS ${colName}`,
  ].join("\n");
}
