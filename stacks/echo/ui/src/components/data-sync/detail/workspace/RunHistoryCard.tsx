import { useState, type ReactNode } from 'react'
import type { RunLike } from '@/lib/types'
import { DEFAULT_RUN_WINDOW, RUN_WINDOWS, RUN_WINDOW_LABEL, runsInWindow, type RunWindow } from '@/lib/runWindow'
import { cn } from '@/lib/utils'
import { DetailCard } from '../DetailCard'
import { RunHistoryChart } from './RunHistoryChart'

interface Props {
  runs: RunLike[]
  onBarClick: (runId: string) => void
  /** Replaces the chart, e.g. when run history is not deployed here. */
  placeholder?: ReactNode
  /** Set with onWindowChange to share the window with the page's figures. */
  window?: RunWindow
  onWindowChange?: (win: RunWindow) => void
}

export function RunHistoryCard({ runs, onBarClick, placeholder, window: controlled, onWindowChange }: Props) {
  const [ownWin, setOwnWin] = useState<RunWindow>(DEFAULT_RUN_WINDOW)
  const win = controlled ?? ownWin
  const choose = (w: RunWindow) => {
    setOwnWin(w)
    onWindowChange?.(w)
  }
  if (placeholder) {
    return <DetailCard title="Run history">{placeholder}</DetailCard>
  }
  return (
    <DetailCard
      title="Run history"
      testid="run-history"
      aside={
        <div
          role="group"
          aria-label="Run history window"
          className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-paper-card p-1 shadow-card"
        >
          {RUN_WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              aria-pressed={w === win}
              onClick={() => choose(w)}
              className={cn(
                'rounded-md px-2.5 py-1 text-meta font-semibold',
                w === win ? 'bg-pearson-purple text-white shadow-card' : 'text-ink-mute hover:text-ink',
              )}
            >
              {RUN_WINDOW_LABEL[w]}
            </button>
          ))}
        </div>
      }
    >
      <RunHistoryChart runs={runsInWindow(runs, win)} onBarClick={onBarClick} />
      <div className="mt-2 flex flex-wrap items-center gap-3 text-label text-ink-mute">
        {/* Same hexes as CHART_STATUS_COLOR, which fills the bars. */}
        <LegendDot color="bg-turq" label="Succeeded" />
        <LegendDot color="bg-fuchsia" label="Failed" />
        <LegendDot color="bg-amber" label="Warning" />
        <LegendDot color="bg-amber" label="Skipped" />
        <LegendDot color="bg-accent" label="Running" />
        <span className="ml-auto">Click a bar to open that run</span>
      </div>
    </DetailCard>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <i className={`inline-block h-2 w-2 rounded-sm ${color}`} />
      {label}
    </span>
  )
}
