import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface RowActionsProps {
  children: ReactNode
  className?: string
}

/** Row actions that show on row hover and on keyboard focus (the row needs `group`). */
export function RowActions({ children, className }: RowActionsProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100',
        className,
      )}
    >
      {children}
    </span>
  )
}

export interface OpenLinkProps {
  to: string
  /** The row's name, for the accessible label "Open {name}". */
  name: string
  className?: string
}

/** The round open icon at a row's right edge. */
export function OpenLink({ to, name, className }: OpenLinkProps) {
  return (
    <Link
      to={to}
      aria-label={`Open ${name}`}
      title="Open"
      className={cn(
        'inline-grid h-8 w-8 place-items-center rounded-full text-accent transition-colors hover:bg-accent-soft',
        className,
      )}
    >
      <ExternalLink size={16} strokeWidth={2} aria-hidden="true" />
    </Link>
  )
}

export interface RowActionsCellProps {
  children?: ReactNode
  open?: { to: string; name: string }
}

/** The actions column: hover actions, then the open icon. */
export function RowActionsCell({ children, open }: RowActionsCellProps) {
  return (
    <div className="flex items-center justify-end gap-1 whitespace-nowrap">
      {children != null && <RowActions>{children}</RowActions>}
      {open && <OpenLink to={open.to} name={open.name} />}
    </div>
  )
}
