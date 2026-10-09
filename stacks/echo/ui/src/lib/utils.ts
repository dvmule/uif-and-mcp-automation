import { type ClassValue, clsx } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'
import { formatDistanceToNow, parseISO, differenceInHours, isValid } from 'date-fns'
import type { Frequency, PipelineConfig, RunStatus } from './types'

// Teach twMerge the @theme scale names; otherwise it reads text-body as a colour and drops text-white.
const twMerge = extendTailwindMerge({
  extend: {
    theme: { radius: ['card', 'brand', 'brand-sm'], shadow: ['card', 'sm-brand'] },
    classGroups: { 'font-size': [{ text: ['label', 'meta', 'body', 'page', 'kpi'] }] },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Lavender chip surface — shared by every status/cloud/layer/freshness pill. */
export const CHIP_SURFACE = 'border-paper-line/60 bg-accent-soft text-ink'

/** Dot color (Tailwind class) for a given run status, used as a chip prefix. */
export function statusDot(status: RunStatus | 'DISABLED' | undefined): string {
  switch (status) {
    case 'SUCCEEDED': return 'bg-turq'
    case 'FAILED':    return 'bg-fuchsia'
    case 'WARNING':   return 'bg-amber'
    case 'RUNNING':   return 'bg-accent pulse-ring text-accent'
    case 'SKIPPED':   return 'bg-amber'
    case 'DISABLED':  return 'bg-paper-line'
    default:          return 'bg-paper-line'
  }
}

export function frequencyHours(freq: Frequency | undefined | null): number {
  switch (freq) {
    case 'hourly': return 1
    case 'daily': return 24
    case 'weekly': return 168
    default: return 24
  }
}

export type FreshnessLevel = 'fresh' | 'stale' | 'critical' | 'unknown'

/** Fresh within one cadence, stale within two, critical beyond. */
export function freshnessFrom(
  enabled: boolean,
  thresholdHours: number,
  lastSuccessAt?: string | null,
): FreshnessLevel {
  if (!enabled) return 'unknown'
  if (!lastSuccessAt) return 'critical'
  const date = parseISO(lastSuccessAt)
  if (!isValid(date)) return 'unknown'
  const hoursSince = differenceInHours(new Date(), date)
  if (hoursSince <= thresholdHours) return 'fresh'
  if (hoursSince <= thresholdHours * 2) return 'stale'
  return 'critical'
}

export function freshness(pipeline: PipelineConfig, lastSuccessAt?: string | null): FreshnessLevel {
  return freshnessFrom(!!pipeline.enabled, frequencyHours(pipeline.schedule?.frequency), lastSuccessAt)
}

/** Dot color (Tailwind class) for a given freshness level, used as a chip prefix. */
export function freshnessDot(level: FreshnessLevel): string {
  switch (level) {
    case 'fresh':    return 'bg-turq'
    case 'stale':    return 'bg-amber'
    case 'critical': return 'bg-fuchsia'
    default:         return 'bg-paper-line'
  }
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true })
  } catch {
    return iso
  }
}

/** Parse an ISO timestamp into ms-since-epoch; null on missing/invalid. */
export function parseDateMs(iso?: string | null): number | null {
  if (!iso) return null
  try {
    const d = parseISO(iso)
    return isValid(d) ? d.getTime() : null
  } catch {
    return null
  }
}

export function formatDuration(secs?: number | null): string {
  if (secs == null) return '—'
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}

export function formatRows(n?: number | null): string {
  if (n == null) return '—'
  return n.toLocaleString()
}

export function tableToId(tableName: string): string {
  return tableName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}

export function suggestWatermarkCol(columns: Array<{ name: string; data_type: string }>): string | null {
  const preferredNames = ['LAST_UPDATE_DATE', 'LAST_UPDATED_DATE', 'UPDATED_AT', 'MODIFIED_DATE', 'LAST_MODIFIED']
  for (const name of preferredNames) {
    if (columns.find(c => c.name.toUpperCase() === name)) return name
  }
  const dateCols = columns.filter(c =>
    ['DATE', 'TIMESTAMP', 'DATETIME'].some(t => c.data_type.toUpperCase().includes(t))
  )
  return dateCols[0]?.name ?? null
}

/** Fallback watermark when the primary column is NULL (e.g. CREATION_DATE for FIPM). */
export function suggestWatermarkFallbackCol(
  columns: Array<{ name: string; data_type: string }>,
  watermarkCol: string | null,
): string | null {
  const preferred = ['CREATION_DATE', 'CREATE_DATE', 'CREATED_AT', 'CREATED_DATE']
  for (const name of preferred) {
    const col = columns.find(c => c.name.toUpperCase() === name)
    if (col && col.name.toUpperCase() !== (watermarkCol ?? '').toUpperCase()) {
      return col.name
    }
  }
  return null
}

export function suggestPkCols(columns: Array<{ name: string; data_type: string }>): string[] {
  return columns
    .filter(c => c.name.toUpperCase().endsWith('_ID') && c.data_type.toUpperCase().includes('NUMBER'))
    .map(c => c.name)
    .slice(0, 3)
}

export function suggestPartitionCol(columns: Array<{ name: string; data_type: string }>): string | null {
  const idCols = columns.filter(
    c => c.name.toUpperCase().endsWith('_ID') &&
      ['NUMBER', 'INTEGER', 'INT', 'BIGINT'].some(t => c.data_type.toUpperCase().includes(t))
  )
  return idCols[0]?.name ?? null
}

export function suggestSoftDeleteCol(columns: Array<{ name: string }>): string | null {
  const names = ['ACTIVE_FLAG', 'STATUS', 'DELETED_FLAG', 'IS_DELETED', 'ACTIVE']
  for (const name of names) {
    if (columns.find(c => c.name.toUpperCase() === name)) return name
  }
  return null
}
