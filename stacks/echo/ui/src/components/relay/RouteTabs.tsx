import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'

export interface RouteTab {
  to: string
  label: string
  end?: boolean
}

export interface RouteTabsProps {
  tabs: RouteTab[]
  /** Accessible name for the tab list. */
  label?: string
  className?: string
}

/** Underline tabs where each tab is a route; NavLink sets aria-current on the active one. */
export function RouteTabs({ tabs, label, className }: RouteTabsProps) {
  return (
    <nav aria-label={label} className={cn('flex gap-1 border-b border-line', className)}>
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              '-mb-px border-b-2 px-3 py-2 text-body font-medium transition-colors',
              isActive
                ? 'border-accent font-semibold text-accent'
                : 'border-transparent text-ink-soft hover:text-ink',
            )
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}
