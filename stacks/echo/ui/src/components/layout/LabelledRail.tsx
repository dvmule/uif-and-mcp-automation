import { Link, useLocation } from 'react-router-dom'
import { ExternalLink, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getEnv, isRuntimeConfigLoaded } from '@/lib/envConfig'
import { normalizePath, under, type RailConfig, type RailLink } from './railConfig'

/** `{env} · {sha}` for the rail foot; the sha only when the build supplied one. */
function versionLine(): string {
  const env = isRuntimeConfigLoaded() ? getEnv() : ''
  const sha = import.meta.env.VITE_BUILD_SHA as string | undefined
  return [env, sha].filter(Boolean).join(' · ')
}

interface Props {
  config: RailConfig
  collapsed: boolean
  onToggle: () => void
}

/** True when this rail row is the current page. */
function isActive(item: RailLink, pathname: string): boolean {
  if (item.external) return false
  if (item.match) return item.match(pathname)
  return item.end ? pathname === item.to : under(pathname, item.to)
}

// Label shown beside an icon when the rail is collapsed: on hover and on
// keyboard focus, so the label is never only a mouse affordance.
function RailTip({ label }: { label: string }) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute left-full top-1/2 z-40 ml-3 -translate-y-1/2 whitespace-nowrap rounded-brand bg-pearson-purple px-2 py-1 text-meta font-semibold text-white opacity-0 shadow-card transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      {label}
    </span>
  )
}

function RailRow({ item, collapsed, pathname }: { item: RailLink; collapsed: boolean; pathname: string }) {
  const Icon = item.icon
  const active = isActive(item, pathname)

  const className = item.quick
    ? cn(
        'group relative mb-3 flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-full bg-pearson-purple text-body font-bold text-white transition-colors hover:bg-accent',
        collapsed ? 'mx-2.5 px-0' : 'mx-3 px-3',
      )
    : cn(
        'group relative flex h-11 items-center gap-3 border-l-[3px] text-sm transition-colors',
        collapsed ? 'justify-center px-0' : 'px-5',
        active
          ? 'border-accent bg-accent-soft font-bold text-accent'
          : 'border-transparent font-medium text-ink hover:bg-accent-soft',
      )

  const body = (
    <>
      <Icon size={17} strokeWidth={1.75} className="shrink-0" />
      {collapsed ? <RailTip label={item.label} /> : item.label}
      {item.external && !collapsed && (
        <ExternalLink size={12} strokeWidth={1.75} className="ml-auto shrink-0 text-ink-mute" aria-hidden="true" />
      )}
    </>
  )

  if (item.external) {
    return (
      <a href={item.to} aria-label={collapsed ? item.label : undefined} className={className}>
        {body}
      </a>
    )
  }

  return (
    <Link
      to={item.to}
      aria-label={collapsed ? item.label : undefined}
      aria-current={active && !item.quick ? 'page' : undefined}
      className={className}
    >
      {body}
    </Link>
  )
}

/** The left menu: icons with labels, collapsible to icons only. */
export function LabelledRail({ config, collapsed, onToggle }: Props) {
  const pathname = normalizePath(useLocation().pathname)
  const version = versionLine()

  return (
    <nav
      aria-label={`${config.heading} menu`}
      className={cn(
        'flex shrink-0 flex-col border-r border-line bg-paper-card transition-[width] duration-200',
        collapsed ? 'w-[64px]' : 'w-[212px]',
      )}
    >
      <div className={cn('pb-2 pt-4', collapsed ? 'text-center' : 'px-5')}>
        <span
          className={cn(
            'text-label font-semibold uppercase tracking-wider text-ink-mute',
            collapsed && 'sr-only',
          )}
        >
          {config.heading}
        </span>
      </div>
      <div className="flex flex-col">
        {config.items.map((entry, i) =>
          'divider' in entry ? (
            <div key={`divider-${i}`} role="separator" className="my-2 border-t border-line" />
          ) : (
            <RailRow key={entry.label} item={entry} collapsed={collapsed} pathname={pathname} />
          ),
        )}
      </div>
      <div className="mt-auto border-t border-line pt-1.5">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
          title={collapsed ? 'Expand menu' : 'Collapse menu'}
          className={cn(
            'flex h-10 w-full items-center gap-3 text-sm font-medium text-ink transition-colors hover:bg-accent-soft',
            collapsed ? 'justify-center px-0' : 'px-5',
          )}
        >
          {collapsed ? (
            <PanelLeftOpen size={17} strokeWidth={1.75} className="shrink-0 text-ink-mute" />
          ) : (
            <PanelLeftClose size={17} strokeWidth={1.75} className="shrink-0 text-ink-mute" />
          )}
          {!collapsed && 'Collapse'}
        </button>
        {!collapsed && version && (
          <p className="px-5 pb-3 pt-1 text-label text-ink-mute" data-testid="rail-version">
            {version}
          </p>
        )}
      </div>
    </nav>
  )
}
