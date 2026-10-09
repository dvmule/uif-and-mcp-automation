// Client for the grain test run service (UIFPE-219, ADR-0008 #5) —
// lambda_grain_probe. Answers the one question no static check can: does
// the author's query return ONE ROW PER ENTITY, against real dev Silver
// data? Vocabulary rule: the UI says "one row per <entity>", never "grain".
//
// Same wiring convention as the AI-draft and submit services: the function
// URL is a constant, filled after the first `terraform apply` prints
// `grain_probe_url`. While it is empty the panel simply offers no test
// button — the battery still runs at submit, so nothing is ungated.

import type { DerivationBlock, WizardState } from "./types";
import { referencedTables } from "./derivedSql";
import { manifestView } from "./manifests";
import { qualifyTable } from "./silverCatalog";

// The deploy passes Terraform's `grain_probe_url` output to the build.
export const GRAIN_PROBE_URL: string = import.meta.env.VITE_GRAIN_PROBE_URL ?? "";

export const probeConfigured = () => GRAIN_PROBE_URL.length > 0;

export interface ProbeResult {
  rowCount: number;
  entityCount: number;
  maxPerEntity: number | null;
  oneRowPerEntity: boolean;
  note: string;
  sample: { columns: string[]; rows: (string | null)[][] };
}

/** Every name the query reads, mapped to the physical db.table Athena can
 *  resolve ({env} placeholder intact — the service substitutes its own
 *  environment): registered aliases from the manifest, task sources by
 *  their declared or catalog-qualified name. Names that map to nothing are
 *  omitted — the panel's advisory checks already flag those. */
export function buildAliasTables(sql: string, state: WizardState):
  Record<string, string> {
  const view = manifestView(state.domain);
  const out: Record<string, string> = {};
  const seen = new Set<string>();
  for (const name of referencedTables(sql)) {
    if (name.includes(".")) continue;               // flagged, not mapped
    // Trino WITH names are case-insensitive: two spellings of one name must
    // yield ONE prelude CTE, or Athena refuses the duplicate
    if (seen.has(name.toUpperCase())) continue;
    seen.add(name.toUpperCase());
    const target = view.aliasTargets.get(name.toUpperCase());
    if (target) {
      out[name] = target;
      continue;
    }
    const src = state.sources.find((s) =>
      (s.table.split(".").pop() ?? "").toUpperCase() === name.toUpperCase());
    if (src) {
      const qualified = src.table.includes(".")
        ? src.table
        : qualifyTable(src.table, state.domain ?? undefined);
      // genuinely unmappable names stay omitted (the contract above) — a
      // bare fallback would earn a jargon refusal from the service instead
      // of the engine's honest table-not-found
      if (qualified?.includes(".")) out[name] = qualified;
    }
  }
  return out;
}

export async function runGrainProbe(block: DerivationBlock,
                                    state: WizardState): Promise<ProbeResult> {
  const r = await fetch(GRAIN_PROBE_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      sql: block.sql,
      keyedBy: block.keyedBy,
      aliasTables: buildAliasTables(block.sql, state),
    }),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok)
    throw new Error((body?.problems ?? [`the test service answered HTTP ${r.status}`])
      .join(" "));
  if (typeof body?.rowCount !== "number" || !body.sample)
    throw new Error("the test service answered in an unexpected shape");
  return body as ProbeResult;
}
