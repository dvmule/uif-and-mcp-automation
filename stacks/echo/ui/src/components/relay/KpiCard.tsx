import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type KpiDot = 'accent' | 'turq' | 'fuchsia' | 'ok'
export type KpiEdge = 'purple' | 'turq' | 'fuchsia' | 'ok'

const DOT_CLASS: Record<KpiDot, string> = {
  accent: 'bg-accent',
  turq: 'bg-turq',
  fuchsia: 'bg-fuchsia-brand',
  ok: 'bg-dot-ok',
}

const EDGE_CLASS: Record<KpiEdge, string> = {
  purple: 'border-t-pearson-purple',
  turq: 'border-t-turq',
  fuchsia: 'border-t-fuchsia-brand',
  ok: 'border-t-signal-ok',
}

export interface KpiCardProps {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  dot?: KpiDot
  edge?: KpiEdge
  /** Sits on the right, e.g. a SuccessRing. */
  ring?: ReactNode
  testid?: string
  /** The figure cannot be read here: value becomes an em dash, the hint says why. */
  unavailable?: boolean
}

/** One figure with a coloured top edge (Beacon's KpiCard); the edge never carries meaning alone. */
export function KpiCard({ label, value, hint, dot = 'accent', edge = 'purple', ring, testid, unavailable = false }: KpiCardProps) {
  const showRing = ring && !unavailable
  return (
    <div
      data-testid={testid}
      className={cn(
        'relative rounded-card border border-t-[3px] border-white/70 bg-paper-card px-5 py-4 shadow-card',
        EDGE_CLASS[edge],
        showRing && 'pr-20',
      )}
    >
      <div className="flex items-center gap-2 text-meta font-bold text-ink">
        <span className={cn('h-2 w-2 rounded-full', DOT_CLASS[dot])} aria-hidden="true" />
        {label}
      </div>
      <div className="tnum mt-1.5 text-kpi font-bold tracking-tight text-accent">{unavailable ? '—' : value}</div>
      {hint && <div className="mt-1.5 text-meta text-ink-mute">{hint}</div>}
      {showRing && <div className="absolute right-5 top-1/2 -translate-y-1/2 leading-none">{ring}</div>}
    </div>
  )
}

export interface SuccessRingProps {
  ok: number
  bad: number
  size?: number
}

/** Share of finished runs that succeeded; decorative, the percentage is in the hint. */
export function SuccessRing({ ok, bad, size = 56 }: SuccessRingProps) {
  const half = size / 2
  const r = size * 0.4
  const c = 2 * Math.PI * r
  const share = ok + bad ? ok / (ok + bad) : 0
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" data-testid="success-ring">
      <circle cx={half} cy={half} r={r} fill="none" stroke="var(--color-dot-fail)" strokeWidth={size * 0.15} />
      <circle
        cx={half}
        cy={half}
        r={r}
        fill="none"
        stroke="var(--color-dot-ok)"
        strokeWidth={size * 0.15}
        strokeDasharray={`${share * c} ${c}`}
        transform={`rotate(-90 ${half} ${half})`}
      />
    </svg>
  )
}

export function KpiRow({ children }: { children: ReactNode }) {
  return <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{children}</div>
}
