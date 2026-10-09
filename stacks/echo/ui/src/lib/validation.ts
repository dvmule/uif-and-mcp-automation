// Client-side mirrors of the controller's destination/config validators.
// Server-side validation in lambda/pipeline_controller.py:
//   _validate_destination_config (L338-405) — the source of truth.
//   _validate_silver_config       (L278-301).
//   _validate_silver_bq_config    (L408-442).
// Keep this file in sync with that source; if the controller rejects a value
// these helpers accept, that's a client/server drift bug.

/** Mirrors the regex at pipeline_controller.py:129 (`_SECRET_ARN_RE`). */
const SECRET_ARN_RE = /^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:.+$/

/** Mirrors pipeline_controller.py:330-336 (`_BQ_LOCATIONS`). */
export const BQ_LOCATIONS = [
  'US', 'EU',
  'us-east1', 'us-east4', 'us-east5',
  'us-west1', 'us-west2', 'us-west3', 'us-west4',
  'us-central1',
  'europe-west1', 'europe-west2', 'europe-west3', 'europe-west4',
  'europe-west6', 'europe-west9', 'europe-north1',
  'asia-east1', 'asia-east2', 'asia-northeast1',
  'asia-southeast1', 'asia-south1', 'australia-southeast1',
] as const

export type BqLocation = typeof BQ_LOCATIONS[number]

const BQ_LOCATION_SET: ReadonlySet<string> = new Set(BQ_LOCATIONS)

export function validateSecretArn(arn: string): string | null {
  if (!arn) return 'Secret ARN is required'
  if (!SECRET_ARN_RE.test(arn)) {
    return 'Secret ARN must look like arn:aws:secretsmanager:<region>:<acct>:secret:<name>'
  }
  return null
}

export function validateGcsPath(path: string, label = 'GCS path'): string | null {
  if (!path) return `${label} is required`
  if (!path.startsWith('gs://')) return `${label} must start with gs://`
  if (!path.endsWith('/'))     return `${label} must end with a trailing /`
  return null
}

export function validateBqLocation(loc: string): string | null {
  if (!loc) return null  // backend defaults to "EU" when empty
  if (!BQ_LOCATION_SET.has(loc)) {
    return `BigQuery location "${loc}" is not in the allowed set`
  }
  return null
}

export function validateBqProject(project: string): string | null {
  if (!project) return 'BigQuery project is required'
  // GCP project IDs: lowercase letters, digits, hyphens; 6-30 chars; can't start/end with hyphen.
  if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(project)) {
    return 'BigQuery project ID must be 6-30 chars (lowercase, digits, hyphens; no leading/trailing hyphen)'
  }
  return null
}

export function validateBqDataset(dataset: string): string | null {
  if (!dataset) return 'BigQuery dataset is required'
  // BQ datasets: letters, digits, underscores; up to 1024 chars; can't start with digit.
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,1023}$/.test(dataset)) {
    return 'BigQuery dataset must be letters/digits/underscores (no leading digit)'
  }
  return null
}
