import type { ReactNode } from 'react'

export interface KvGroupProps {
  title: ReactNode
  children: ReactNode
  testid?: string
}

/** A titled list of key/value rows; stack groups with space-y-4. */
export function KvGroup({ title, children, testid }: KvGroupProps) {
  return (
    <div data-testid={testid}>
      <h3 className="mb-1 border-b border-line pb-1.5 text-label font-bold uppercase tracking-wider text-ink-mute">
        {title}
      </h3>
      <dl>{children}</dl>
    </div>
  )
}

export interface KvProps {
  label: ReactNode
  children: ReactNode
}

/** One label / value line (Beacon's Kv). */
export function Kv({ label, children }: KvProps) {
  return (
    <div className="grid grid-cols-[150px_minmax(0,1fr)] items-baseline gap-3 border-b border-line py-2 text-body last:border-b-0">
      <dt className="text-meta font-semibold text-ink-mute">{label}</dt>{' '}
      <dd className="min-w-0 text-ink">{children}</dd>
    </div>
  )
}
