// The Silver layer's Glue catalog — every *silver* database across every
// domain, so cross-domain sources and cross-catalog joins are first-class.
// MVP bridge: a dev-time snapshot (scripts/snapshot_silver_catalog.py, re-run
// with credentials to refresh); the live read API replaces the import without
// changing this module's interface. The UI shows capturedAt so staleness is
// visible, and free-text entry always works for tables the snapshot missed.
import snapshot from "./silver-catalog.json";

export interface CatalogColumn { name: string; type: string }
export interface CatalogTable { name: string; columns: CatalogColumn[] }
export interface CatalogDatabase { database: string; tables: CatalogTable[] }

export const CAPTURED_AT: string = (snapshot as any).capturedAt;

export const SILVER_DATABASES: CatalogDatabase[] =
  ((snapshot as any).databases as CatalogDatabase[])
    .filter((d) => d.tables.length > 0);

export function findTable(qualified: string): CatalogTable | undefined {
  const i = qualified.lastIndexOf(".");
  if (i < 0) return undefined;
  const db = SILVER_DATABASES.find((d) => d.database === qualified.slice(0, i)
    // configs write env-tokenized names; the snapshot holds concrete ones
    || d.database === qualified.slice(0, i).replace("{env}", "dev"));
  return db?.tables.find((t) => t.name === qualified.slice(i + 1));
}

// Resolve a BARE source name (the intake records PDH names like
// MTL_SYSTEM_ITEMS_B) to a qualified Silver table, preferring the echo_dev_*
// catalogs. Returns undefined when the snapshot has no match — caller keeps
// the bare name and the column pickers fall back to free text.
export function qualifyTable(bare: string, preferDomain?: string): string | undefined {
  const want = bare.toLowerCase();
  const hits: string[] = [];
  for (const db of SILVER_DATABASES)
    for (const t of db.tables)
      if (t.name.toLowerCase() === want) hits.push(`${db.database}.${t.name}`);
  // A bare PDH name can exist in SEVERAL Silver databases (mtl_system_items_b
  // is in four), so the order below decides which one an author is sent to.
  //
  // Silver is now keyed by SOURCE SYSTEM, not by domain: dev's domain-keyed
  // databases became uif_echo_dev_silver_mdm and _tep, and the old ones are
  // being retired. So a `_silver_{domain}` preference can no longer match, and
  // "echo_dev" actively prefers a database that is going away — uif_echo_dev
  // does not start with it. Hence: the standard catalogs first, then the source
  // system that serves the entity's domain, then the legacy forms.
  const SOURCE_SYSTEM: Record<string, string> = {
    product: "mdm", program: "mdm", marketing_copy: "mdm",
    price: "tep", stock: "tep",
  };
  const domain = preferDomain?.toLowerCase();
  const system = domain ? SOURCE_SYSTEM[domain] : undefined;
  const systemDb = system ? `_silver_${system}` : null;
  const domainDb = domain ? `_silver_${domain}` : null;
  const has = (s: string, part: string | null) => Number(!!part && s.includes(part));
  hits.sort((a, b) =>
    Number(b.startsWith("uif_echo_")) - Number(a.startsWith("uif_echo_"))
    || has(b, systemDb) - has(a, systemDb)
    || has(b, domainDb) - has(a, domainDb)
    || Number(b.startsWith("echo_dev")) - Number(a.startsWith("echo_dev"))
    || a.localeCompare(b));
  return hits[0];
}


// Columns of a table for the Columns step's pickers. Accepts bare PDH names
// (resolved via qualifyTable) or qualified ones; UPPERCASED to match the
// intake convention (Oracle-style column names). Empty when the snapshot
// doesn't know the table — callers fall back to free text.
export function columnsFor(table: string): string[] {
  if (!table) return [];
  const qualified = table.includes(".") ? table : qualifyTable(table);
  if (!qualified) return [];
  return (findTable(qualified)?.columns ?? []).map((c) => c.name.toUpperCase());
}

// UIFPE-260: a scaffolded entity's manifest/pipeline must carry the Silver
// table with the ENVIRONMENT templated (echo_{env}_silver_x.y) — gold rows
// must not hardcode dev names into other environments. The snapshot's
// database names are per-env; template the env segment out.
export function envTemplateSilver(qualified: string): string | null {
  const i = qualified.lastIndexOf(".");
  if (i <= 0) return null;
  const db = qualified.slice(0, i);
  const templated = db.replace(/_(dev|uat|stg|qa|prod|us1)_/, "_{env}_");
  return templated === db ? null : `${templated}${qualified.slice(i)}`;
}

// UIFPE-262: the scaffolder needs a qualified, env-templated Silver name for
// every LOOKUP table a nested field reads — collected here from the authored
// columns so the Review step can put them on the intake. Tables the catalog
// cannot resolve (or without an env marker) are simply omitted; the compiler
// then refuses with the table named, which the preview shows verbatim.
export function lookupSilverTables(
    columns: { kind: string; fields?: { source?: { table?: string } | null }[] }[],
    drivingTable: string | null,
    preferDomain?: string | null): Record<string, string> {
  const drivingBare =
    (drivingTable ?? "").split(".").pop()?.toLowerCase() ?? "";
  const out: Record<string, string> = {};
  for (const c of columns) {
    if (c.kind !== "nested") continue;
    for (const f of c.fields ?? []) {
      const t = f.source?.table ?? "";
      const bare = t.split(".").pop() ?? "";
      if (!bare || bare.toLowerCase() === drivingBare) continue;
      const qualified = t.includes(".")
        ? t : qualifyTable(bare, preferDomain ?? undefined);
      const templated = qualified ? envTemplateSilver(qualified) : null;
      if (templated) out[bare.toUpperCase()] = templated;
    }
  }
  return out;
}
