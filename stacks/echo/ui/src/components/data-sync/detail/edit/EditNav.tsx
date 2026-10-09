import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { EDIT_SECTIONS } from './editSections'
import type { Violation } from './validation'

interface Props {
  pipelineId: string
  changedSections: Record<string, number>
  violations: Violation[]
}

export function EditNav({ pipelineId, changedSections, violations }: Props) {
  return (
    <nav aria-label="Edit sections" className="flex flex-col gap-0.5">
      {EDIT_SECTIONS.map(s => {
        const changed = changedSections[s.key] ?? 0
        const err = violations.some(v => v.severity === 'err' && v.section === s.key)
        return (
          <NavLink
            key={s.key}
            to={`/m/data-sync/syncs/${pipelineId}/edit/${s.key}`}
            className={({ isActive }) =>
              cn(
                'flex items-center justify-between gap-2 rounded-brand-sm px-2.5 py-1.5 text-body font-semibold transition-colors',
                isActive ? 'bg-accent-soft text-accent' : 'text-ink-mute hover:text-ink',
              )
            }
          >
            <span>{s.label}</span>
            {err ? (
              <span className="rounded-full bg-signal-fail/10 px-1.5 text-[10px] font-bold text-signal-fail">!</span>
            ) : changed > 0 ? (
              <span className="rounded-full border border-accent bg-accent-soft px-1.5 text-[10px] font-bold text-accent">{changed}</span>
            ) : null}
          </NavLink>
        )
      })}
    </nav>
  )
}
