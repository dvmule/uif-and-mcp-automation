// One status vocabulary for every screen: label, tone and icon per status.
// Pure module, no React components. Screens render it through relay/StatusBadge.
import {
  AlarmClock,
  AlertTriangle,
  Bell,
  BellCheck,
  BellOff,
  BellRing,
  CheckCircle2,
  CircleCheck,
  Clock,
  HeartPulse,
  HelpCircle,
  Loader2,
  MinusCircle,
  PauseCircle,
  Sparkles,
  StopCircle,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { normalizeRunStatus } from './runLifecycle'
import type { FreshnessLevel } from './utils'
import type { GoldDetailStatus } from './goldDetail'
import type { DqRunStatus, PipelineStatus } from './types'

export type Tone = 'ok' | 'warn' | 'fail' | 'live' | 'mute'

export interface StatusView {
  label: string
  tone: Tone
  icon: LucideIcon
  /** The wire value, kept for the tooltip. */
  raw: string
}

/** Soft-pill classes per tone: tinted ground, text in the tone's colour. */
export const TONE_CLASS: Record<Tone, string> = {
  ok: 'bg-signal-ok-bg text-signal-ok',
  warn: 'bg-signal-warn-bg text-signal-warn',
  fail: 'bg-signal-fail-bg text-signal-fail',
  live: 'bg-signal-live-bg text-signal-live',
  mute: 'bg-pearson-mist text-ink-soft',
}

const rawOf = (value: string | null | undefined): string => value ?? ''

export function runStatusView(
  status: string | null | undefined,
  opts: { goldNoChanges?: boolean } = {},
): StatusView {
  const raw = rawOf(status)
  if (!status) return { label: 'Unknown', tone: 'mute', icon: HelpCircle, raw }
  // STOPPED is an operator action, not a Glue failure, so it is neutral, not Failed
  if (status.toUpperCase() === 'STOPPED') return { label: 'Stopped', tone: 'mute', icon: StopCircle, raw }
  switch (normalizeRunStatus(status)) {
    case 'SUCCEEDED':
      return {
        label: opts.goldNoChanges ? 'Succeeded · no changes' : 'Succeeded',
        tone: 'ok',
        icon: CheckCircle2,
        raw,
      }
    case 'WARNING':
      return { label: 'Warning', tone: 'warn', icon: AlertTriangle, raw }
    case 'RUNNING':
      return { label: 'Running', tone: 'live', icon: Loader2, raw }
    case 'SKIPPED':
      return { label: 'Skipped', tone: 'mute', icon: MinusCircle, raw }
    default:
      return { label: 'Failed', tone: 'fail', icon: XCircle, raw }
  }
}

export function pipelineStatusView(status: PipelineStatus | undefined): StatusView {
  if (status === 'DISABLED') return { label: 'Disabled', tone: 'mute', icon: PauseCircle, raw: status }
  return runStatusView(status)
}

export function syncStatusView(
  status: 'healthy' | 'degraded' | 'failing' | 'running' | 'disabled',
): StatusView {
  switch (status) {
    case 'healthy':
      return { label: 'Healthy', tone: 'ok', icon: HeartPulse, raw: status }
    case 'degraded':
      return { label: 'Degraded', tone: 'warn', icon: AlertTriangle, raw: status }
    case 'failing':
      return { label: 'Failing', tone: 'fail', icon: XCircle, raw: status }
    case 'running':
      return { label: 'Running', tone: 'live', icon: Loader2, raw: status }
    case 'disabled':
      return { label: 'Disabled', tone: 'mute', icon: PauseCircle, raw: status }
  }
}

/** A gold entity's health: the sync words, plus Unknown when there are no runs to judge by. */
export function goldStatusView(status: GoldDetailStatus): StatusView {
  if (status === 'unknown') return { label: 'Unknown', tone: 'mute', icon: HelpCircle, raw: status }
  return syncStatusView(status)
}

/** The flag beside a sync whose last ingest run is well past its cadence. */
export const BEHIND_SCHEDULE_VIEW: StatusView = {
  label: 'Behind schedule',
  tone: 'warn',
  icon: Clock,
  raw: 'stale',
}

export function freshnessView(level: FreshnessLevel, opts: { neverRan?: boolean } = {}): StatusView {
  switch (level) {
    case 'fresh':
      return { label: 'Fresh', tone: 'ok', icon: CircleCheck, raw: level }
    case 'stale':
      return { label: 'Stale', tone: 'warn', icon: Clock, raw: level }
    case 'critical':
      return {
        label: opts.neverRan ? 'No successful run yet' : 'Overdue',
        tone: 'fail',
        icon: AlarmClock,
        raw: level,
      }
    default:
      return { label: 'Unknown', tone: 'mute', icon: HelpCircle, raw: level }
  }
}

export function dqRunStatusView(status: DqRunStatus | undefined): StatusView {
  const raw = rawOf(status)
  switch (status) {
    case 'SUCCEEDED':
      return { label: 'Passed', tone: 'ok', icon: CheckCircle2, raw }
    case 'FAILED':
      return { label: 'Failed', tone: 'fail', icon: XCircle, raw }
    case 'SKIPPED':
      return { label: 'Skipped', tone: 'mute', icon: MinusCircle, raw }
    case 'RUNNING':
      return { label: 'Running', tone: 'live', icon: Loader2, raw }
    case 'NOT_IMPLEMENTED':
      return { label: 'Coming soon', tone: 'mute', icon: Sparkles, raw }
    default:
      return { label: 'Unknown', tone: 'mute', icon: HelpCircle, raw }
  }
}

/** CloudWatch alarm states; anything else is shown as its own sentence-cased text. */
export function alertRuleStatusView(status: 'OK' | 'ALARM' | 'INSUFFICIENT_DATA' | string): StatusView {
  switch (status) {
    case 'OK':
      return { label: 'OK', tone: 'ok', icon: BellCheck, raw: status }
    case 'ALARM':
      return { label: 'In alarm', tone: 'fail', icon: BellRing, raw: status }
    case 'INSUFFICIENT_DATA':
      return { label: 'No data yet', tone: 'mute', icon: BellOff, raw: status }
    default: {
      const words = (status || 'unknown').toLowerCase().replace(/_/g, ' ')
      return { label: words.charAt(0).toUpperCase() + words.slice(1), tone: 'mute', icon: Bell, raw: status }
    }
  }
}
