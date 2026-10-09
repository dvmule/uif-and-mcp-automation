// Small pieces the New replication steps share: tooltip hint, confidence badge, field hint/error, section.
import type { ReactNode } from 'react'
import { Info, Zap } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { TONE_CLASS } from '@/lib/statusVocabulary'
import { cn } from '@/lib/utils'

export function Hint({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Info size={14} className="ml-1 inline cursor-help text-ink-mute" />
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  )
}

export function ConfidenceBadge({ level }: { level: 'high' | 'medium' }) {
  return (
    <span
      className={cn(
        'ml-1 inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-label font-bold',
        level === 'high' ? TONE_CLASS.ok : TONE_CLASS.warn,
      )}
    >
      <Zap size={10} aria-hidden="true" />
      {level === 'high' ? 'High confidence' : 'Suggested'}
    </span>
  )
}

export function FieldError({ children }: { children: ReactNode }) {
  return <p className="text-meta text-signal-fail">{children}</p>
}

export function FieldHint({ children }: { children: ReactNode }) {
  return <p className="text-meta text-ink-mute">{children}</p>
}

/** A sub-heading inside a step card that groups related fields. */
export function Section({ title, sub, children }: { title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-4 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <div>
        <h4 className="text-body font-semibold text-ink">{title}</h4>
        {sub && <p className="text-meta text-ink-mute">{sub}</p>}
      </div>
      {children}
    </section>
  )
}
