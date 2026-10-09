import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
  CartesianGrid,
} from 'recharts'
import { format, parseISO } from 'date-fns'
import type { RunLike } from '@/lib/types'
import { formatRunKindLabel } from '@/lib/runKindLabel'
import { CHART_STATUS_COLOR, PEARSON } from '@/lib/tokens'
import { RunHistoryTooltip } from './RunHistoryTooltip'

interface Props {
  runs: RunLike[]
  onBarClick: (runId: string) => void
  /**
   * Optional explicit dimensions. When omitted, the chart fills its parent
   * via ResponsiveContainer. Tests should pass explicit dims because
   * ResponsiveContainer renders zero-width inside jsdom (no real layout).
   */
  width?: number
  height?: number
}

function tickRows(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1_000_000) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1e3).toFixed(1)}K`
  return n.toLocaleString()
}

export function RunHistoryChart({ runs, onBarClick, width, height }: Props) {
  const data = runs
    .slice(0, 30)
    .slice()
    .reverse()
    .map((r, i) => ({
      i,
      run_id: r.run_id,
      label: r.started_at ? format(parseISO(r.started_at), 'MMM d HH:mm') : `#${i + 1}`,
      rows: r.rows_read ?? r.rows_extracted ?? 0,
      rowsWritten: r.rows_written ?? 0,
      duration: r.duration_sec ?? 0,
      status: r.status,
      kindLabel: formatRunKindLabel(r),
      watermark: r.new_watermark,
    }))
  const avg = data.length ? Math.round(data.reduce((s, d) => s + d.rows, 0) / data.length) : 0

  // In jsdom, ResponsiveContainer has no layout and renders zero bars,
  // so fall back to explicit dimensions when supplied. Default test path
  // uses 400x160 so the bar geometry materializes and rect[data-status]
  // is queryable.
  const useExplicit = typeof width === 'number' && typeof height === 'number'
  const isTest =
    typeof process !== 'undefined' && process.env?.NODE_ENV === 'test'
  const w = width ?? 400
  const h = height ?? 160

  const chart = (
    <BarChart
      data={data}
      width={useExplicit || isTest ? w : undefined}
      height={useExplicit || isTest ? h : undefined}
      margin={{ top: 8, right: 8, bottom: 4, left: 4 }}
    >
      <CartesianGrid stroke={PEARSON.line} vertical={false} />
      <XAxis
        dataKey="label"
        tick={{ fontSize: 10, fill: PEARSON.inkMute }}
        axisLine={false}
        tickLine={false}
        interval="preserveStartEnd"
      />
      <YAxis
        tickFormatter={tickRows}
        tick={{ fontSize: 10, fill: PEARSON.inkMute }}
        axisLine={false}
        tickLine={false}
        width={48}
      />
      <Tooltip content={<RunHistoryTooltip />} cursor={{ fill: PEARSON.mist, opacity: 0.5 }} />
      {avg > 0 && (
        <ReferenceLine
          y={avg}
          stroke={PEARSON.amethyst}
          strokeDasharray="4 3"
          strokeOpacity={0.6}
        />
      )}
      <Bar
        dataKey="rows"
        radius={[2, 2, 0, 0]}
        maxBarSize={20}
        isAnimationActive={false}
        onClick={data => {
          // Recharts passes a BarRectangleItem whose `payload` is the
          // original row from the chart data array.
          const row = data?.payload as { run_id?: string } | undefined
          if (row?.run_id) onBarClick(row.run_id)
        }}
      >
        {data.map(d => (
          <Cell
            key={d.run_id}
            fill={
              CHART_STATUS_COLOR[d.status as keyof typeof CHART_STATUS_COLOR] ??
              PEARSON.lightPurple
            }
            stroke={PEARSON.white}
            strokeWidth={1}
            data-status={d.status}
          />
        ))}
      </Bar>
    </BarChart>
  )

  if (useExplicit || isTest) {
    return <div className="h-40">{chart}</div>
  }
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        {chart}
      </ResponsiveContainer>
    </div>
  )
}
