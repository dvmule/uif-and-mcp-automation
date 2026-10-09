import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RunDots } from '../RunDots'
import { pipelineRunRow, type RunRow } from '@/lib/runsTable'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const pipeline = { pipeline_id: 'p1', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig

const run = (id: string, status: string, hour: number): RunRow =>
  pipelineRunRow(pipeline, {
    job_name: 'uif-echo-dev-bronze-ingest',
    run_id: id,
    pipeline_id: 'p1',
    status,
    started_at: `2026-09-29T${String(hour).padStart(2, '0')}:02:00Z`,
  } as RunMetrics)

const dots = (container: HTMLElement) => Array.from(container.querySelectorAll('[role="img"] > span'))

describe('RunDots', () => {
  it('pads missing runs on the left with dashed circles and puts the newest last', () => {
    const { container } = render(<RunDots runs={[run('jr_a', 'SUCCEEDED', 10), run('jr_b', 'FAILED', 14)]} />)
    const all = dots(container)
    expect(all).toHaveLength(5)
    expect(all.slice(0, 3).every((d) => d.getAttribute('title') === 'no run')).toBe(true)
    expect(all[0]).toHaveClass('border-dashed', 'border-line')
    expect(all[3]).toHaveAttribute('title', 'Succeeded · 29 Sep 2026, 10:02 UTC · jr_a')
    expect(all[4]).toHaveAttribute('title', 'Failed · 29 Sep 2026, 14:02 UTC · jr_b')
    expect(all[4]).toHaveAttribute('aria-label', 'Failed · 29 Sep 2026, 14:02 UTC · jr_b')
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Last 2 runs, oldest first: Succeeded, Failed')
  })

  it('rings only the newest dot, in its own colour, at 26px', () => {
    const { container } = render(<RunDots runs={[run('a', 'SUCCEEDED', 10), run('b', 'FAILED', 14)]} />)
    const [, , , older, newest] = dots(container)
    expect(newest).toHaveClass('h-[26px]', 'w-[26px]', 'outline', 'outline-2', 'outline-offset-2', 'outline-dot-fail', 'bg-dot-fail')
    expect(older).toHaveClass('h-5', 'w-5', 'bg-dot-ok')
    expect(older).not.toHaveClass('outline')
  })

  it('maps each status to its token fill and glyph', () => {
    const { container } = render(
      <RunDots
        runs={[
          run('ok', 'SUCCEEDED', 1),
          run('warn', 'WARNING', 2),
          run('bad', 'FAILED', 3),
          run('skip', 'SKIPPED', 4),
          run('live', 'RUNNING', 5),
        ]}
      />,
    )
    const [ok, warn, bad, skip, live] = dots(container)
    expect(ok).toHaveClass('bg-dot-ok', 'text-white')
    expect(ok).toHaveTextContent('✓')
    expect(warn).toHaveClass('bg-signal-warn-line')
    expect(warn).toHaveTextContent('!')
    expect(bad).toHaveClass('bg-dot-fail')
    expect(bad).toHaveTextContent('✕')
    expect(skip).toHaveClass('bg-dot-mute')
    expect(skip).toHaveTextContent('–')
    expect(live).toHaveClass('animate-spin', 'motion-reduce:animate-none', 'border-t-signal-live', 'outline-signal-live')
    expect(live).toHaveTextContent('')
  })

  it('shows only the last five when given more, and five dashed circles when given none', () => {
    const many = Array.from({ length: 7 }, (_, i) => run(`r${i}`, 'SUCCEEDED', i + 1))
    const { container, unmount } = render(<RunDots runs={many} />)
    expect(dots(container).map((d) => d.getAttribute('title')?.split(' · ')[2])).toEqual(['r2', 'r3', 'r4', 'r5', 'r6'])
    unmount()
    const empty = render(<RunDots runs={[]} />)
    expect(dots(empty.container).every((d) => d.getAttribute('title') === 'no run')).toBe(true)
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'No runs')
  })

  it('uses no hand-coded colour', () => {
    const { container } = render(<RunDots runs={[run('a', 'RUNNING', 1)]} />)
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,6}/i)
  })
})
