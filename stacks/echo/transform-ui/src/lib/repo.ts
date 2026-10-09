// Cross-domain discovery: every registered intake mapping in the monorepo,
// found by glob — add a domain's mapping/<entity>/v1/<domain>.mapping.json
// and it appears here
// with zero UI changes. This is what makes the wizard domain-agnostic: the
// UI renders the intake CONTRACT (scalar/conditional/nested), never a
// domain's topology.
import type {
  IntakeMapping, MappingColumn, RowGroupDef, TransformDef, EntityKeyPair,
} from "./types";

const found = import.meta.glob(
  "../../../../../domains/*/src/schemas/mapping/*/v1/*.mapping.json",
  { eager: true }) as Record<string, IntakeMapping>;

export interface DomainMapping {
  domain: string;
  table: string;
  drivingTable: string | null;
  entityKey: EntityKeyPair[];
  rowGroups: Record<string, RowGroupDef>;
  transforms: Record<string, TransformDef>;
  columns: MappingColumn[];
  path: string;
}

export const REGISTERED: DomainMapping[] = Object.entries(found).map(
  ([path, m]) => ({
    domain: (m as any).domain,
    table: (m as any).table,
    drivingTable: (m as any).drivingTable ?? null,
    entityKey: (m as any).entityKey ?? [],
    rowGroups: (m as any).rowGroups ?? {},
    transforms: (m as any).transforms ?? {},
    columns: (m as any).columns,
    path: path.replace(/^(\.\.\/)+/, "domains/"),
  }));

export const DOMAINS = [...new Set(REGISTERED.map((r) => r.domain))];

// The monorepo's SETTLED domain list (repo rule: new domains beyond these
// need framework-owner confirmation + a create-domain.sh stamp). DERIVED at
// build time from the repository itself — every domain folder carries the
// mould's DOMAIN-CHECKLIST.md — so the fifteenth domain can never silently
// miss the picker the way program/price/stock once did (UIFPE-219; the list
// was a hand-maintained constant that went stale at seven).
export const SETTLED_DOMAINS = Object.keys(
  import.meta.glob("../../../../../domains/*/DOMAIN-CHECKLIST.md"),
).map((p) => {
  const seg = p.split("/").at(-2)!;
  // Vite normalizes the path of the domain THIS app lives in
  // (domains/product/ui) to plain "../../../DOMAIN-CHECKLIST.md" — the
  // hosting domain is the one name the glob cannot spell out.
  return seg === ".." ? "product" : seg;
}).sort();

// Source tables already used by a domain's registered mappings — offered as
// SUGGESTIONS in the Sources step, never as the only choices.
export function knownSourceTables(domain: string): string[] {
  const tables = new Set<string>();
  for (const reg of REGISTERED.filter((r) => r.domain === domain)) {
    for (const col of reg.columns) {
      if (col.kind === "scalar" && col.source?.table) tables.add(col.source.table);
      if (col.kind === "conditional")
        col.branches?.forEach((b) => b.source?.table && tables.add(b.source.table));
      if (col.kind === "nested")
        col.fields?.forEach((f) => f.source?.table && tables.add(f.source.table));
    }
  }
  return [...tables].sort();
}
