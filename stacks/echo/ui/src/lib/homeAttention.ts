// What the Home page's "Needs attention" card lists, built from the recent-activity
// feed and the gold live stats. Pure, so every rule here is tested.
import type { ActivityItem } from './types'
import type { GoldLiveWindow } from './goldStats'
import { normalizeRunStatus } from './runLifecycle'
import { freshnessView, runStatusView, type StatusView } from './statusVocabulary'
import { parseDateMs } from './utils'

export const MAX_ATTENTION_ROWS = 10
export const GOLD_PATH = '/m/data-sync/gold'

/** The rail's words for the area a job belongs to; unknown modules show as sent. */
export function areaLabel(module: string): string {
  if (module === 'sync') return 'Replicate'
  if (module === 'export') return 'Export'
  if (module === 'dq') return 'Quality'
  return module
}

/** `HH:MM` in UTC, or empty when there is no usable date. */
export function formatClockUtc(iso: string | null | undefined): string {
  const ms = parseDateMs(iso)
  return ms == null ? '' : new Date(ms).toISOString().slice(11, 16)
}

export interface AttentionRow {
  key: string
  subject: string
  area: string
  message: string
  view: StatusView
  /** Null for a gold row: the stats call names the table, not a time. */
  ts: string | null
  href: string
}

export interface Attention {
  /** At most MAX_ATTENTION_ROWS. */
  rows: AttentionRow[]
  /** Before the cap. */
  total: number
  /** Rows the cap left out. */
  more: number
}

type Kind = 'failed' | 'stopped' | null

function kindOf(status: string): Kind {
  const upper = (status ?? '').toUpperCase()
  if (upper === 'STOPPED') return 'stopped'
  // A quality check that is not built yet is not a failure.
  if (upper === 'NOT_IMPLEMENTED' || upper === '') return null
  return normalizeRunStatus(status) === 'FAILED' ? 'failed' : null
}

/** Newest item per (module, subject). The feed is newest first, so on a tie or an
 *  unreadable date the earlier item wins. */
function newestPerSubject(items: ActivityItem[]): ActivityItem[] {
  const newest = new Map<string, ActivityItem>()
  for (const item of items) {
    const key = `${item.module}|${item.subject}`
    const held = newest.get(key)
    if (!held || (parseDateMs(item.ts) ?? -Infinity) > (parseDateMs(held.ts) ?? -Infinity)) {
      newest.set(key, item)
    }
  }
  return [...newest.values()]
}

function byNewest(a: AttentionRow, b: AttentionRow): number {
  return (parseDateMs(b.ts) ?? -Infinity) - (parseDateMs(a.ts) ?? -Infinity)
}

/** Failed and stopped objects (newest state of each), then stale gold tables. */
export function buildAttention(
  items: ActivityItem[],
  goldLive?: Pick<GoldLiveWindow, 'stale_entities'> | null,
): Attention {
  const failed: AttentionRow[] = []
  const stopped: AttentionRow[] = []
  for (const item of newestPerSubject(items)) {
    const kind = kindOf(item.status)
    if (!kind) continue
    const row: AttentionRow = {
      key: `${item.module}|${item.subject}`,
      subject: item.subject,
      area: areaLabel(item.module),
      message: item.message ?? '',
      view: runStatusView(item.status),
      ts: item.ts,
      href: item.link_path,
    }
    if (kind === 'failed') failed.push(row)
    else stopped.push(row)
  }
  failed.sort(byNewest)
  stopped.sort(byNewest)

  const stale: AttentionRow[] = (goldLive?.stale_entities ?? []).map((name) => ({
    key: `gold|${name}`,
    subject: name,
    area: 'Transform',
    message: 'no successful run inside its cadence',
    view: freshnessView('stale'),
    ts: null,
    href: GOLD_PATH,
  }))

  const all = [...failed, ...stopped, ...stale]
  return {
    rows: all.slice(0, MAX_ATTENTION_ROWS),
    total: all.length,
    more: Math.max(0, all.length - MAX_ATTENTION_ROWS),
  }
}
