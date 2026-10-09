import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'

interface DetailCardProps {
  /** Omit, with no aside, for a card without a header row. */
  title?: ReactNode
  /** Right side of the header: a short sub line, a link or a button. */
  aside?: ReactNode
  children: ReactNode
  /** A bar under the body, on the head colour (Save / Discard, for one). */
  footer?: ReactNode
  /** Classes for the body; defaults to p-4. */
  bodyClassName?: string
  testid?: string
}

/** The card every detail-page section sits in: white, soft white border, card radius and shadow. */
export function DetailCard({ title, aside, children, footer, bodyClassName, testid }: DetailCardProps) {
  return (
    <section
      data-testid={testid}
      className="mb-4 overflow-hidden rounded-card border border-white/70 bg-paper-card shadow-card"
    >
      {(title || aside) && (
        <div className="flex items-center justify-between gap-3 px-4 py-2.5">
          <h3 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h3>
          {aside && <div className="text-meta text-ink-mute">{aside}</div>}
        </div>
      )}
      <div className={bodyClassName ?? 'p-4'}>{children}</div>
      {footer && (
        <div className="flex items-center justify-between gap-3 border-t border-line bg-paper-head px-4 py-3">
          {footer}
        </div>
      )}
    </section>
  )
}

/** "Replicate › EDS.PRICE_LIST" above a detail page's title; `via` adds a linked middle step. */
export function DetailBreadcrumb({
  to,
  label,
  current,
  via,
}: {
  to: string
  label: string
  current: string
  via?: { to: string; label: string }
}) {
  return (
    <nav aria-label="Breadcrumb" className="mb-1.5 text-meta text-ink-mute">
      <Link to={to} className="font-semibold text-accent hover:underline">
        {label}
      </Link>{' '}
      ›{' '}
      {via && (
        <>
          <Link to={via.to} className="break-all font-semibold text-accent hover:underline">
            {via.label}
          </Link>{' '}
          ›{' '}
        </>
      )}
      <span className="break-all">{current}</span>
    </nav>
  )
}

/** A header-row text link in the accent colour. */
export function CardLink({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <Link to={to} className={cn('font-semibold text-accent hover:underline', className)}>
      {children}
    </Link>
  )
}
