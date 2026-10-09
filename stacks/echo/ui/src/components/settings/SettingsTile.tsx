import { Link } from 'react-router-dom'
import { ChevronRight, type LucideIcon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

export interface SettingsTileProps {
  icon: LucideIcon
  label: string
  description: string
  to: string
  disabled?: boolean
  /** Badge label shown when disabled. Defaults to "later". */
  badge?: string
}

/** One row of the Settings list card: icon, label, description and a chevron. */
export function SettingsTile({
  icon: Icon,
  label,
  description,
  to,
  disabled = false,
  badge,
}: SettingsTileProps) {
  const testId = `settings-tile-${label.toLowerCase().replace(/\s+/g, '-')}`

  const body = (
    <div
      data-testid={testId}
      aria-disabled={disabled || undefined}
      className={cn(
        'flex items-center gap-3.5 px-5 py-3.5 transition-colors',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-paper-head',
      )}
    >
      <span className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full bg-accent-soft text-accent">
        <Icon size={18} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-[14px] font-bold text-ink">{label}</p>
          {disabled && (
            <Badge variant="secondary" className="text-[10px] uppercase tracking-wide">
              {badge ?? 'later'}
            </Badge>
          )}
        </div>
        <p className="text-[12.5px] text-ink-soft">{description}</p>
      </div>
      {!disabled && <ChevronRight size={16} aria-hidden="true" className="ml-auto shrink-0 text-accent" />}
    </div>
  )

  if (disabled) {
    return <div className="border-b border-line last:border-b-0">{body}</div>
  }
  return (
    <Link
      to={to}
      className="block border-b border-line last:border-b-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
    >
      {body}
    </Link>
  )
}
