// Secret ARNs as the Sources and Connections pages show them, and which syncs use each. Pure: no React.
import type { PipelineConfig } from './types'

export type ConnectionKind = 'oracle' | 'gcs' | 'silver-bq'

const UNKNOWN_ARN = '(unknown)'

/** The secret's name: the part after `:secret:`, without Secrets Manager's 6-character suffix. */
export function secretName(arn: string): string {
  const at = arn.indexOf(':secret:')
  const name = at >= 0 ? arn.slice(at + ':secret:'.length) : (arn.split(':').pop() ?? arn)
  return name.replace(/-[A-Za-z0-9]{6}$/, '') || arn
}

/** `…secret:name-AbC123`: the readable end of an ARN; anything else is returned as is. */
export function arnTail(arn: string): string {
  const at = arn.indexOf(':secret:')
  return at > 0 ? `…${arn.slice(at + 1)}` : arn
}

export interface SourceGroup {
  arn: string
  name: string
  syncs: Array<{ id: string; table: string }>
}

/** Syncs grouped by the Oracle secret they read, by source name. */
export function groupSourcesBySecret(pipelines: PipelineConfig[]): SourceGroup[] {
  const byArn = new Map<string, SourceGroup>()
  for (const p of pipelines) {
    const arn = p.source?.secret_arn || UNKNOWN_ARN
    const group = byArn.get(arn) ?? { arn, name: arn === UNKNOWN_ARN ? arn : secretName(arn), syncs: [] }
    group.syncs.push({ id: p.pipeline_id, table: p.extract?.table || p.pipeline_id })
    byArn.set(arn, group)
  }
  const groups = Array.from(byArn.values())
  for (const g of groups) g.syncs.sort((a, b) => a.table.localeCompare(b.table))
  return groups.sort((a, b) => a.name.localeCompare(b.name))
}

/** The secret a pipeline reads for one connection kind, when its config names one. */
function secretFor(p: PipelineConfig, kind: ConnectionKind): string | undefined {
  if (kind === 'oracle') return p.source?.secret_arn
  if (kind === 'gcs') return p.replication?.gcs?.credentials_secret_arn
  return p.replication?.silver_bq?.credentials_secret_arn
}

/** How many syncs use this secret. Null for a GCS or BigQuery secret when no loaded
 *  sync has that replication block at all, so the page can leave the count out. */
export function connectionUsage(pipelines: PipelineConfig[], kind: ConnectionKind, arn: string): number | null {
  const withBlock = pipelines.filter((p) => secretFor(p, kind) !== undefined)
  if (kind !== 'oracle' && withBlock.length === 0) return null
  return withBlock.filter((p) => secretFor(p, kind) === arn).length
}
