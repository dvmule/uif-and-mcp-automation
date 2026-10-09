import { useEffect, useState } from 'react'

const GRACE_MS = 200

/**
 * Thin indeterminate bar for "something is loading". Drive `active` from the
 * caller (wiring it to the API client is a later phase). It appears only after
 * a 200 ms grace so quick calls never flicker, and sits in a zero-height
 * wrapper so nothing shifts.
 */
export function TopLoader({ active }: { active: boolean }) {
  const [pastGrace, setPastGrace] = useState(false)
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setPastGrace(true), GRACE_MS)
    return () => {
      clearTimeout(t)
      setPastGrace(false)
    }
  }, [active])
  if (!active || !pastGrace) return null
  return (
    <div className="relative h-0" role="progressbar" aria-label="Loading">
      <div className="absolute inset-x-0 top-0 z-20 h-[2.5px] overflow-hidden">
        <div className="toploader-bar absolute top-0 h-full rounded-full bg-accent" />
      </div>
    </div>
  )
}
