import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'

export interface ObjectHeaderProps {
  back: { to: string; label: string }
  /** Required unless `loading`. */
  title?: ReactNode
  /** Pills and status badges, inside the h1 after the title. */
  pills?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  /** A quiet line under the actions. */
  caption?: ReactNode
  loading?: boolean
}

/** A detail page's header in Beacon's object shape: back link, title with pills, meta line, actions. Never renders section tabs. */
export function ObjectHeader({ back, title, pills, meta, actions, caption, loading = false }: ObjectHeaderProps) {
  const backLink = (
    <Link to={back.to} className="inline-flex items-center gap-1 text-meta font-semibold text-accent hover:underline">
      <ArrowLeft size={13} aria-hidden="true" /> {back.label}
    </Link>
  )
  if (loading) {
    return (
      <div className="mb-5" aria-busy="true">
        {backLink}
        <span className="sr-only" role="status">
          Loading…
        </span>
        <div aria-hidden="true" className="mt-1.5">
          <Skeleton className="h-6 w-64 max-w-full bg-paper-line/60" />
          <Skeleton className="mt-2 h-4 w-96 max-w-full bg-paper-line/40" />
        </div>
      </div>
    )
  }
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {backLink}
        <h1 className="mt-1.5 flex flex-wrap items-center gap-2.5 text-[24px] font-semibold leading-tight tracking-tight text-ink">
          <span className="break-all">{title}</span>
          {pills}
        </h1>
        {meta && <p className="mt-1 text-meta text-ink-mute">{meta}</p>}
      </div>
      {(actions || caption) && (
        <div className="flex flex-col items-end gap-1.5">
          {actions && <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>}
          {caption && <span className="text-meta text-ink-mute">{caption}</span>}
        </div>
      )}
    </div>
  )
}
