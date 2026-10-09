import type { ReactNode } from 'react'

export interface CardHeadProps {
  title: ReactNode
  /** A control or caption on the right, e.g. a WindowSelect. */
  right?: ReactNode
}

/** A card's 18px heading with an optional right slot (ported from Beacon). */
export function CardHead({ title, right }: CardHeadProps) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
      {right}
    </div>
  )
}
