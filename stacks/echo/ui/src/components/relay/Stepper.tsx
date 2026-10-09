import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface StepperStep {
  label: string
  /** Shown under the label once the step is done, e.g. "Source: FIPM.X". */
  answer?: string
  /** Shown under the label while the step is not done yet, e.g. "prefilled: db.table". */
  hint?: string
}

export interface StepperProps {
  steps: StepperStep[]
  /** Zero-based index of the active step. */
  current: number
  /** When set, finished steps become buttons that jump back to that step. */
  onStepClick?: (index: number) => void
  className?: string
}

export function Stepper({ steps, current, onStepClick, className }: StepperProps) {
  return (
    <ol className={cn('flex items-center gap-3', className)}>
      {steps.map((step, i) => {
        const done = i < current
        const active = i === current
        const body = (
          <>
            <span
              className={cn(
                'grid h-6 w-6 shrink-0 place-items-center rounded-full text-label font-bold',
                done && 'bg-signal-ok text-white',
                active && 'bg-accent text-white',
                !done && !active && 'bg-paper-head text-ink-soft',
              )}
            >
              {done ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : i + 1}
            </span>
            <span className="flex min-w-0 flex-col text-left">
              <span className={cn('text-body', active ? 'font-semibold text-ink' : 'text-ink-soft')}>
                {step.label}
              </span>
              {done && step.answer && <span className="truncate text-meta text-ink-mute">{step.answer}</span>}
              {!done && step.hint && <span className="truncate text-label text-ink-mute">{step.hint}</span>}
            </span>
          </>
        )
        return (
          <li
            key={step.label}
            aria-current={active ? 'step' : undefined}
            className="flex flex-1 items-center gap-3 last:flex-none"
          >
            {done && onStepClick ? (
              <button
                type="button"
                onClick={() => onStepClick(i)}
                className="flex items-center gap-2 rounded-brand"
              >
                {body}
              </button>
            ) : (
              <span className="flex items-center gap-2">{body}</span>
            )}
            {i < steps.length - 1 && (
              <span aria-hidden="true" className={cn('h-px flex-1', done ? 'bg-signal-ok/60' : 'bg-line')} />
            )}
          </li>
        )
      })}
    </ol>
  )
}
