import { useState, type ReactNode } from 'react'
import { Info } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface InfoTipProps {
  text: ReactNode
  /** Accessible name of the button. */
  label?: string
}

/** A description behind an ⓘ: shows on hover and focus, pins on click (ported from Beacon). */
export function InfoTip({ text, label = 'About this section' }: InfoTipProps) {
  const [pinned, setPinned] = useState(false)
  if (!text) return null
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label}
        aria-expanded={pinned}
        onClick={() => setPinned((p) => !p)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setPinned(false)
        }}
        onBlur={() => setPinned(false)}
        className={cn(
          'grid h-[26px] w-[26px] place-items-center rounded-full border transition-colors',
          pinned
            ? 'border-accent bg-accent-soft text-accent'
            : 'border-line bg-paper-card text-ink-mute hover:border-accent hover:bg-accent-soft hover:text-accent',
        )}
      >
        <Info size={13} strokeWidth={2} aria-hidden="true" />
      </button>
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-0 top-full z-40 mt-2 w-[min(460px,80vw)] rounded-[10px] bg-pearson-purple px-3.5 py-3 text-body font-medium normal-case leading-relaxed tracking-normal text-white shadow-[0_8px_24px_rgb(13_0_77_/_0.3)] transition-opacity duration-150 before:absolute before:-top-1.5 before:left-2 before:border-x-[6px] before:border-b-[6px] before:border-x-transparent before:border-b-pearson-purple before:content-[''] group-hover:opacity-100 group-focus-within:opacity-100",
          pinned ? 'opacity-100' : 'opacity-0',
        )}
      >
        {text}
      </span>
    </span>
  )
}
