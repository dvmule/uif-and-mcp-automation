// Registered runtime sources per domain (UIFPE-219), read at build time from
// each entity's mapping manifest — the same file the transform runtime reads.
// This is what separates a REAL source (registered alias, watermark-tracked,
// loaded every run) from a contract-first placeholder a mapping merely names
// (the column ships, its values stay empty until the source is wired in).
// Parsed with the same two-line shape the Python gate greps
// (derivation_chain.manifest_aliases / registered_tables) — no YAML parser
// needed for two flat keys.
import { findTable, qualifyTable } from "./silverCatalog";

const RAW: Record<string, string> = import.meta.glob(
  "../../../../../domains/*/src/schemas/mapping/*/v1/manifest.yaml",
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const byDomain = new Map<string, Set<string>>();
const aliasesByDomain = new Map<string, Set<string>>();
const aliasByTableByDomain = new Map<string, Map<string, string>>();
const aliasTargetsByDomain = new Map<string, Map<string, string>>();
for (const [path, text] of Object.entries(RAW)) {
  // …/<domain>/src/schemas/… — the wildcard segment right before /src/
  const m = /([^/]+)\/src\/schemas\//.exec(path);
  const domain = m?.[1] === ".." ? "product" : m?.[1];
  if (!domain) continue;
  const tables = byDomain.get(domain) ?? new Set<string>();
  for (const t of text.matchAll(/silver_table:\s*(\S+)/g))
    tables.add((t[1].split(".").pop() ?? "").toUpperCase());
  byDomain.set(domain, tables);
  // the runtime VIEW names a derived query reads by — same regex as the
  // Python battery's manifest_aliases — paired with the physical table each
  // one reads (the `silver_table:` on the same source entry), so the panel
  // can say "this entity reads X as items_ext" instead of a bare refusal
  const aliases = aliasesByDomain.get(domain) ?? new Set<string>();
  const byTable = aliasByTableByDomain.get(domain) ?? new Map<string, string>();
  const targets = aliasTargetsByDomain.get(domain) ?? new Map<string, string>();
  for (const a of text.matchAll(
    /^\s*- alias:\s*(\w+)(?:\r?\n(?!\s*- ).*)*?\r?\n\s*silver_table:\s*(\S+)/gm)) {
    aliases.add(a[1].toUpperCase());
    const bare = (a[2].split(".").pop() ?? "").toUpperCase();
    if (bare && !byTable.has(bare)) byTable.set(bare, a[1]);
    // the FULL db.table target, {env} placeholder intact — the grain-probe
    // service substitutes the environment it runs in
    if (!targets.has(a[1].toUpperCase())) targets.set(a[1].toUpperCase(), a[2]);
  }
  aliasesByDomain.set(domain, aliases);
  aliasByTableByDomain.set(domain, byTable);
  aliasTargetsByDomain.set(domain, targets);
}

/** Upper-cased registered runtime ALIASES (the temp-view names) of a
 *  domain's manifests — what a derived query's FROM/JOINs may name, in
 *  addition to the task's own declared sources — plus the table→alias map
 *  for the "use this name instead" hint. */
export function manifestView(domain: string | null | undefined):
  { aliases: Set<string>; aliasByTable: Map<string, string>;
    aliasTargets: Map<string, string> } {
  return {
    aliases: (domain && aliasesByDomain.get(domain)) || new Set(),
    aliasByTable: (domain && aliasByTableByDomain.get(domain)) || new Map(),
    aliasTargets: (domain && aliasTargetsByDomain.get(domain)) || new Map(),
  };
}

/** Upper-cased bare names of every Silver table the domain's manifests
 *  register as a runtime source. Empty set = no manifest yet (draft domain). */
export function registeredSilverTables(domain: string): Set<string> {
  return byDomain.get(domain) ?? new Set();
}

/**
 * Where a source table STANDS, as the one definition both the wizard's
 * Sources step and the standalone Sources view render. They asked this
 * question separately before and disagreed — one keyed on the catalog, the
 * other on the manifest, behind pills that looked the same.
 *
 *   ok         a registered runtime source (or, for a domain with no manifest
 *              yet, a table the Silver catalog does have)
 *   unknown    the name matches no Silver table at all — a typo, or a table
 *              nobody has ingested; the value can never arrive
 *   not-wired  it exists in Silver but is not a runtime source for this
 *              entity yet, so attributes reading it ship empty until the feed
 *              is wired in (the contract-first case)
 */
export function sourceStanding(table: string, domain: string):
  "ok" | "unknown" | "not-wired" {
  const short = (table.split(".").pop() ?? table);
  if (!findTable(table) && !qualifyTable(short)) return "unknown";
  const runtime = registeredSilverTables(domain);
  // A domain with no manifest cannot say anything about wiring — the catalog
  // check above is then the only honest signal (silence here is what let the
  // two screens contradict each other).
  if (!runtime.size) return "ok";
  return runtime.has(short.toUpperCase()) ? "ok" : "not-wired";
}
