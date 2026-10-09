import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { TRANSFORM_UI_NEW_PATH } from '@/lib/echoPaths'
import { PipelineDrawing } from './PipelineDrawing'
import { RangeControl } from './RangeControl'

export interface WelcomeCardProps {
  username?: string
  windowHours?: number
  /** The summary's update time, "HH:MM" UTC. */
  updated?: string
  /** The FigureStrip along the card's foot. */
  children: ReactNode
}

/** Home's opening card: purple greeting half, then the health strip. */
export function WelcomeCard({ username, windowHours, updated, children }: WelcomeCardProps) {
  return (
    <div className="relative mb-5 rounded-card bg-paper-card shadow-card">
      <div className="echo-welcome relative flex items-center justify-between gap-6 overflow-hidden rounded-t-card px-8 py-6">
        <div className="echo-welcome-hex" aria-hidden="true" />
        <div className="relative min-w-0">
          <div className="text-label font-bold uppercase tracking-[0.08em] text-pearson-light-purple">
            Replication and gold tables
          </div>
          <h1 className="mt-1.5 text-[28px] font-bold leading-tight tracking-tight text-white">
            {username ? `Welcome, ${username}.` : 'Welcome.'}
          </h1>
          <p className="mt-1.5 max-w-xl text-body text-white/80">
            Health of every sync and gold table over the last {windowHours ?? 24} hours.
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              to="/m/data-sync/syncs/new"
              className="inline-flex h-10 items-center gap-2 rounded-full bg-white px-5 text-body font-bold text-pearson-purple transition-colors hover:bg-pearson-mist"
            >
              <Plus size={14} aria-hidden="true" /> New replication
            </Link>
            <a
              href={TRANSFORM_UI_NEW_PATH}
              className="inline-flex h-10 items-center gap-2 rounded-full border-[1.5px] border-white px-5 text-body font-bold text-white transition-colors hover:bg-white/10"
            >
              New transformation
            </a>
          </div>
        </div>
        <PipelineDrawing />
      </div>

      <div className="relative flex flex-wrap items-center justify-between gap-3 border-t border-line bg-paper-head px-8 py-2.5">
        <span className="text-meta font-bold text-ink-soft">
          Health
          {windowHours ? ` · last ${windowHours} hours` : ''}
          {updated ? ` · updated ${updated} UTC` : ''}
        </span>
        <RangeControl />
      </div>

      {children}
    </div>
  )
}
