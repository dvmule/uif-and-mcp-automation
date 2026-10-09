/**
 * TypeScript types mirroring the backend Pydantic schemas.
 * Keep aligned with backend/app/schemas/schema.py.
 */

export type DataClassification = 'public' | 'internal' | 'confidential' | 'restricted';

export type Lifecycle = 'active' | 'deprecated';

export interface SchemaOwner {
  service: string | null;
  team: string | null;
  email: string | null;
}

export interface SchemaSummary {
  artifact_id: string;
  group_id: string;
  name: string | null;
  type: string;
  state: string;
  created_on: string | null;
  modified_on: string | null;
  latest_version: string | null;
  owner: SchemaOwner;
  domain: string | null;
  classification: DataClassification | null;
  lifecycle: Lifecycle | null;
  pii_fields: string[];
  /** Stub — W2 field; artifact_id of the schema that supersedes this one when deprecated */
  successor_artifact_id?: string | null;
  /** Stub — W2 field; ISO date when the deprecated schema is scheduled for removal */
  retirement_date?: string | null;
  /** Stub — W2 field; service IDs consuming this schema (CMDB consumes manifest) */ // @stub
  consumers?: string[] | null;
  /** Stub — W2 field; total version count from Apicurio history */ // @stub
  version_count?: number | null;
}


export interface SchemaListResponse {
  items: SchemaSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface SchemaVersionRead {
  version: string;
  state: string;
  created_on: string | null;
  created_by: string | null;
}

export interface SchemaDetailRead extends SchemaSummary {
  content: string;
  content_version: string;
  versions: SchemaVersionRead[];
  compatibility: string | null;
  /** Stub — W2 field; backend will populate once audit log is wired */
  last_breaking_change_review_date?: string | null;
  /** Stub — W2 field; populated once CMDB consumes manifest is indexed */
  consumers?: string[];
}

export interface ValidationFailure {
  rule: string;
  message: string;
}

export interface ValidationResult {
  artifact_id: string;
  group_id: string;
  passed: boolean;
  failures: ValidationFailure[];
  validated_at: string;
}

export interface NonCompliantResponse {
  items: ValidationResult[];
  last_validated: string | null;
  total_checked: number;
}

export interface SchemaServiceRef {
  ci_id: string;
  urn: string;
  name: string;
  domain: string;
}

export interface SchemaServicesRead {
  artifact_id: string;
  group_id: string;
  producers: SchemaServiceRef[];
  consumers: SchemaServiceRef[];
}
