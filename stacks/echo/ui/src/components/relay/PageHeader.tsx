import type { ReactNode } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { InfoTip } from './InfoTip'
import { usePageTabs } from './PageTabsContext'
import { RouteTabs } from './RouteTabs'

export interface PageHeaderProps {
  /** Required unless `loading`. */
  title?: ReactNode
  sub?: ReactNode
  /** The page's description behind an ⓘ after the title. */
  info?: ReactNode
  actions?: ReactNode
  /** A back link or breadcrumb shown above the title. */
  back?: ReactNode
  /** Pass false to skip the section tabs the shell provides. */
  tabs?: false
  /** The page's record is still loading: two skeleton bars stand in for title and sub (no heading). */
  loading?: boolean
  className?: string
}

export function PageHeader({
  title,
  sub,
  info,
  actions,
  back,
  tabs: showTabs,
  loading = false,
  className,
}: PageHeaderProps) {
  const pageTabs = usePageTabs()
  const tabs =
    pageTabs && showTabs !== false ? <RouteTabs tabs={pageTabs} label="Section tabs" className="mt-3" /> : null

  if (loading) {
    return (
      <div className={cn('mb-5', className)} aria-busy="true">
        {back}
        <span className="sr-only" role="status">
          Loading…
        </span>
        <div aria-hidden="true">
          <Skeleton className="h-6 w-64 max-w-full bg-paper-line/60" />
          <Skeleton className="mt-2 h-4 w-96 max-w-full bg-paper-line/40" />
        </div>
        {tabs}
      </div>
    )
  }
  return (
    <div className={cn('mb-5', className)}>
      {back}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="text-page font-extrabold tracking-tight text-ink">{title}</h1>
            {info && (
              <span className="align-middle">
                <InfoTip
                  text={info}
                  label={typeof title === 'string' ? `About ${title}` : 'About this page'}
                />
              </span>
            )}
          </div>
          {sub && <p className="mt-1 max-w-3xl text-body text-ink-mute">{sub}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {tabs}
    </div>
  )
}
