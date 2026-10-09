// New-table artifacts (UIFPE-219): the schema-first setup flow's one unique
// capability, folded into the ONE wizard's Review step. When a task CREATES
// a gold table, the compiler refuses the envelope until the table's
// master.json exists (schema registry first) — so Review generates that
// schema from the task's own columns, ready to download and register.
//
// The property encoding MIRRORS compile_intake_delta.py's master_prop() —
// same JSON type map, same value-array vs struct-array rule, same
// nullability and x-channels shape. A schema this file emits must be one the
// compiler would have written and the repo's schema CI would accept; the UI
// data types (date/timestamp/float) are NOT JSON Schema types, so they are
// translated here exactly as the compiler translates them.
import type { DataType, MappingColumn, WizardState } from "./types";
import { artifactPaths } from "./masterSchema";

// compile_intake_delta.py JSON_TYPE
const JSON_TYPE: Record<DataType, string> = {
  string: "string", integer: "integer", number: "number", float: "number",
  boolean: "boolean", date: "string", timestamp: "string",
};
// Draft-07 `format` keeps the intent the JSON type erases: date/timestamp
// (JSON "string"), and float (JSON "number", else DDL derives exact DECIMAL).
const JSON_FORMAT: Partial<Record<DataType, string>> = {
  date: "date", timestamp: "date-time", float: "double",
};

const tiersOf = (c: MappingColumn["channels"]) =>
  (["event", "message", "api"] as const).filter((k) => c[k]);

const typed = (t: DataType, nullable: boolean) => {
  const j = JSON_TYPE[t] ?? "string";
  // key order matters: the compiler's master_prop emits type, description,
  // format, x-channels — callers splice description between type and format
  const out: Record<string, unknown> = { type: nullable ? [j, "null"] : j };
  if (JSON_FORMAT[t]) out.format = JSON_FORMAT[t];
  return out;
};

function propFor(col: MappingColumn): Record<string, unknown> {
  const channels = tiersOf(col.channels);
  if (col.kind === "nested") {
    // Registered encodings: ONE field is a VALUE array (webConsumingSystem);
    // several fields are a STRUCT array (contributors). Arrays are nullable
    // at the column level — the query's COALESCE(..., array()) decides
    // emptiness downstream.
    const fields = col.fields ?? [];
    const items = fields.length === 1
      ? typed(fields[0].type as DataType, false)
      : {
          type: "object",
          additionalProperties: false,
          properties: Object.fromEntries(fields.map((f) =>
            [f.name, typed(f.type as DataType, true)])),
        };
    return { type: ["array", "null"], description: col.businessDescription,
             items, "x-channels": channels };
  }
  // A conditional declares no nullability — the compiler's master_prop reads
  // it as nullable by default (col.get("nullable", True)); mirror that.
  const nullable = col.kind === "scalar" ? col.nullable !== false : true;
  const { type, format } = typed(col.dataType as DataType, nullable) as
    { type: unknown; format?: string };
  return {
    // master_prop's key order exactly: type, description, format, x-channels
    type,
    description: col.businessDescription,
    ...(format ? { format } : {}),
    "x-channels": channels,
  };
}

export interface NewTableArtifacts {
  paths: ReturnType<typeof artifactPaths>;
  masterJson: string;
  /** Entity-key columns the task has not declared as columns — the schema
   *  cannot type them, so they need declaring before the table is real. */
  missingKeyColumns: string[];
  /** Blocking reasons the generated schema would be invalid or incomplete. */
  problems: string[];
}

export function newTableArtifacts(state: WizardState): NewTableArtifacts {
  const entity = (state.destinationTable ?? "").split(".").pop() ?? "table";
  const domain = state.domain ?? "domain";
  const declared = new Set(state.columns.map((c) => c.goldColumnName));
  const keyPairs = (state.entityKey ?? []).filter((p) => p.goldColumn);
  const keyNames = keyPairs.map((p) => p.goldColumn);
  // The metadata the REGISTRY CONTRACT requires, checked by
  // validate_gold_schemas.py's check_master: owner.service, domain,
  // dataClassification, audience (exactly "engineering" — a master is the
  // engineering artifact; consumer subsets are derived), lifecycle. A
  // generated file that omits them fails the repo's own gate, so they are
  // emitted here: derived where derivable, defaulted to the house value for
  // classification, and the contact taken from the Review step's
  // "Requested by".
  const owner: Record<string, string> = {
    service: `urn:pearson:service:${domain}:${entity}-svc`,
    team: "Enterprise Data Connect",
  };
  if (state.requestedBy?.trim()) owner.contact = state.requestedBy.trim();
  const titled = entity.split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  const master = {
    $schema: "http://json-schema.org/draft-07/schema#",
    $id: `https://schemas.pearson.com/gold/${entity}/v1/master.json`,
    registry: { group: domain, artifact: `${domain}-gold-master`,
                compatibility: "BACKWARD" },
    title: titled,
    description: `Gold entity: ${titled}. Compiled by the UIFPE-260 `
      + `scaffolder from the authored intake `
      + `(mapping/${entity}/v1/${domain}.mapping.json) — that file is the `
      + `source of truth for the rules. x-channels declares which `
      + `downstream tier(s) consume each attribute.`,
    metadata: {
      owner,
      domain,
      entity,
      version: "v1",
      // The key in the shape program/price declare it — pairs, not names.
      // Normalized key order (goldColumn first): the drivingColumn <select>
      // handler builds pairs the other way round, and the compiler's master
      // must serialize identically.
      primaryKey: keyPairs.map((p) => ({
        goldColumn: p.goldColumn, drivingColumn: p.drivingColumn })),
      dataClassification: "internal",
      audience: "engineering",
      // New tables start DRAFT: a draft master may lack a manifest until its
      // Silver sources are real (the validator's lifecycle-gated skip).
      lifecycle: "draft",
    },
    type: "object",
    additionalProperties: false,
    // the compiler's rule: every non-nullable column, in column order —
    // key columns are non-nullable by the scaffolder's own guard
    required: state.columns
      .filter((c) => c.goldColumnName
        && c.kind === "scalar" && c.nullable === false)
      .map((c) => c.goldColumnName),
    properties: Object.fromEntries(
      state.columns.filter((c) => c.goldColumnName && !c.goldColumnName.includes("."))
        .map((c) => [c.goldColumnName, propFor(c)])),
  };
  const missingKeyColumns = keyNames.filter((k) => !declared.has(k));
  const problems: string[] = [];
  if (!keyNames.length)
    problems.push("this new table has no key declared — go back to Gold table "
      + "and say which column(s) identify one row");
  if (missingKeyColumns.length)
    problems.push(`the key column${missingKeyColumns.length === 1 ? "" : "s"} `
      + `${missingKeyColumns.join(", ")} `
      + `${missingKeyColumns.length === 1 ? "is" : "are"} not declared as `
      + "columns yet — add them in Columns so the schema can type them");
  return {
    paths: artifactPaths(domain, entity),
    masterJson: JSON.stringify(master, null, 2) + "\n",
    missingKeyColumns,
    problems,
  };
}
