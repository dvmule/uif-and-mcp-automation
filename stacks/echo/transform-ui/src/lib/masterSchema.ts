// master.json as the PRIMARY artifact.
//
// The registered gold schema (domains/<domain>/src/schemas/gold/<entity>/v1/
// master.json) is the contract published to the Schema Registry, so it — not the
// intake mapping — is what a gold table IS: its attributes, their types, their
// consumer tiers. This module discovers every registered master across every
// domain the same way repo.ts discovers mappings, so the UI reads the primary
// artifact directly and stays domain-agnostic.
//
// Division of labour this assumes:
//   master.json    WHAT attributes exist, their type, tier and business meaning
//   *.mapping.json HOW each attribute is populated — mapping rules only
//   *.sql          generated from the mapping; never authored
//
// One gap to know about, surfaced rather than papered over: master.json does not
// declare a primary key. Its `required` list is a JSON-Schema presence rule, not
// a key — on the product entity `required` carries three names while the key is
// two (ppid is required but is not part of the grain). Until master.json gains a
// key declaration, `primaryKeyOf` resolves it from the mapping and reports which
// source it used, so a screen can say where the answer came from instead of
// implying master.json knew.

import { REGISTERED } from "./repo";
import type { EntityKeyPair } from "./types";

const found = import.meta.glob("../../../../../domains/*/src/schemas/gold/*/*/master.json",
  { eager: true }) as Record<string, unknown>;

export type JsonScalarType = "string" | "integer" | "number" | "boolean"
  | "array" | "object";

export interface MasterAttribute {
  name: string;
  /** Declared JSON type with any null union removed. */
  type: JsonScalarType;
  /** True when the schema's type union includes "null". */
  nullable: boolean;
  /** Business meaning, straight from the schema. */
  description: string;
  /** Consumer tiers (x-channels). Empty = internal only. */
  channels: string[];
  /** JSON-Schema `required` membership — presence, NOT key membership. */
  required: boolean;
  /** Nested shape for arrays/objects, when the schema declares one. */
  children: MasterAttribute[];
}

export interface MasterEntity {
  domain: string;
  /** Entity folder name, e.g. "product". */
  entity: string;
  /** Schema folder version, e.g. "v1". */
  version: string;
  title: string;
  description: string;
  /** Repo-relative path to the file this came from. */
  path: string;
  metadata: Record<string, unknown>;
  attributes: MasterAttribute[];
  requiredNames: string[];
}

/** Strip a `null` union and name the underlying JSON type. */
function normalizeType(t: unknown): { type: JsonScalarType; nullable: boolean } {
  const list = Array.isArray(t) ? t : [t];
  const nullable = list.includes("null");
  const real = list.find((x) => x !== "null");
  const known: JsonScalarType[] =
    ["string", "integer", "number", "boolean", "array", "object"];
  return { nullable,
           type: known.includes(real as JsonScalarType)
             ? (real as JsonScalarType) : "string" };
}

function readAttribute(name: string, raw: any, requiredNames: string[]): MasterAttribute {
  const { type, nullable } = normalizeType(raw?.type);
  // Arrays describe their element in `items`; objects in `properties`.
  const itemProps = type === "array"
    ? (raw?.items?.properties ?? null) : (raw?.properties ?? null);
  const childRequired: string[] = (type === "array"
    ? raw?.items?.required : raw?.required) ?? [];
  return {
    name, type, nullable,
    description: raw?.description ?? "",
    channels: Array.isArray(raw?.["x-channels"]) ? raw["x-channels"] : [],
    required: requiredNames.includes(name),
    children: itemProps
      ? Object.entries(itemProps).map(([k, v]) => readAttribute(k, v, childRequired))
      : [],
  };
}

/**
 * Parse one discovered master.json.
 *
 * The DOMAIN comes from the schema's own `metadata.domain`, not from the file
 * path — Vite normalizes a glob key and collapses the segment the `*` matched
 * into the `../` chain, so `<domain>/src/schemas/...` arrives as `src/schemas/...`
 * with the domain gone. (repo.ts sidesteps the same trap by reading `domain` out
 * of the mapping JSON.) A schema without `metadata.domain` is therefore not
 * discoverable, which is a reasonable thing to require of the primary artifact.
 */
export function readEntity(path: string, raw: any): MasterEntity | null {
  const clean = path.replace(/\\/g, "/");
  const m = /\/gold\/([^/]+)\/([^/]+)\/master\.json$/.exec(clean);
  if (!m) return null;
  const [, entity, version] = m;
  const domain = (raw?.metadata?.domain as string | undefined)
    // fallback for a schema with no metadata.domain: only usable when the path
    // still carries a real segment (it will not for the importer's own domain)
    ?? /\/([^/.]+)\/src\/schemas\/gold\//.exec(clean)?.[1];
  if (!domain) return null;
  const requiredNames: string[] = raw?.required ?? [];
  return {
    domain, entity, version,
    title: raw?.title ?? entity,
    description: raw?.description ?? "",
    // rebuilt from the repo's layout rather than the collapsed glob key
    path: artifactPaths(domain, entity, version).master,
    metadata: raw?.metadata ?? {},
    requiredNames,
    attributes: Object.entries(raw?.properties ?? {})
      .map(([k, v]) => readAttribute(k, v, requiredNames)),
  };
}

/** Every registered gold schema in the monorepo, any domain, any entity. */
export const MASTERS: MasterEntity[] = Object.entries(found)
  .map(([path, raw]) => readEntity(path, raw))
  .filter((x): x is MasterEntity => !!x)
  .sort((a, b) => a.domain.localeCompare(b.domain) || a.entity.localeCompare(b.entity));

export const MASTER_DOMAINS: string[] = [...new Set(MASTERS.map((m) => m.domain))];

export function mastersFor(domain: string): MasterEntity[] {
  return MASTERS.filter((m) => m.domain === domain);
}

export function findMaster(domain: string, entity: string): MasterEntity | undefined {
  return MASTERS.find((m) => m.domain === domain && m.entity === entity);
}

/** Where a gold table's key actually comes from today. */
export type KeySource = "master" | "mapping" | "none";

export interface ResolvedKey {
  key: EntityKeyPair[];
  source: KeySource;
  /** True when master.json's `required` disagrees with the resolved key — the
   *  reason `required` must not be read as a key declaration. */
  requiredDiffers: boolean;
}

/**
 * Resolve an entity's primary key, preferring a declaration on master.json once
 * one exists (metadata.primaryKey) and falling back to the intake mapping's
 * entityKey. Reports the source so a screen can be honest about it.
 */
export function primaryKeyOf(domain: string, entity: string): ResolvedKey {
  const master = findMaster(domain, entity);
  const declared = (master?.metadata as any)?.primaryKey;
  const mapping = REGISTERED.find((r) =>
    r.domain === domain && r.table.split(".").pop() === entity);
  const fromMapping = mapping?.entityKey ?? [];

  const key: EntityKeyPair[] = Array.isArray(declared) && declared.length
    ? declared.map((d: any) => typeof d === "string"
        ? { goldColumn: d, drivingColumn: "" }
        : { goldColumn: d.goldColumn, drivingColumn: d.drivingColumn ?? "" })
    : fromMapping;

  const source: KeySource = Array.isArray(declared) && declared.length ? "master"
    : fromMapping.length ? "mapping" : "none";

  const req = [...(master?.requiredNames ?? [])].sort().join(",");
  const keyNames = key.map((p) => p.goldColumn).sort().join(",");
  return { key, source, requiredDiffers: !!req && !!keyNames && req !== keyNames };
}

/** Attributes of a registered entity that the mapping has no rule for yet —
 *  the work list when master.json leads and the mapping follows. */
export function unmappedAttributes(domain: string, entity: string): string[] {
  const master = findMaster(domain, entity);
  if (!master) return [];
  const mapping = REGISTERED.find((r) =>
    r.domain === domain && r.table.split(".").pop() === entity);
  const mapped = new Set((mapping?.columns ?? []).map((c) =>
    c.goldColumnName.split(".")[0]));
  return master.attributes.map((a) => a.name).filter((n) => !mapped.has(n));
}

/** Mapping rules with no attribute in master.json — the opposite drift. */
export function unregisteredMappings(domain: string, entity: string): string[] {
  const master = findMaster(domain, entity);
  const mapping = REGISTERED.find((r) =>
    r.domain === domain && r.table.split(".").pop() === entity);
  if (!master || !mapping) return [];
  const known = new Set(master.attributes.map((a) => a.name));
  return [...new Set(mapping.columns
    .map((c) => c.goldColumnName.split(".")[0])
    .filter((n) => !known.has(n)))];
}

/** The four artifact paths for a domain + entity, per the repo's layout. Used
 *  by the generate step so the target files are stated, never guessed. */
export function artifactPaths(domain: string, entity: string, version = "v1") {
  const base = `domains/${domain}/src/schemas`;
  return {
    master: `${base}/gold/${entity}/${version}/master.json`,
    mapping: `${base}/mapping/${entity}/${version}/${domain}.mapping.json`,
    manifest: `${base}/mapping/${entity}/${version}/manifest.yaml`,
    sql: `${base}/mapping/${entity}/${version}/sql/${entity}.sql`,
  };
}
