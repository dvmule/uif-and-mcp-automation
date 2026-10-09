import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CalendarClock, SearchX } from 'lucide-react'
import {
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  FacetSelect,
  ListCard,
  Note,
  PageHeader,
  RowActionsCell,
  StatusBadge,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  WindowSelect,
  type DataTableColumn,
} from '@/components/relay'
import { Button } from '@/components/ui/button'
import { useAllRuns } from '@/components/data-sync/runs/useAllRuns'
import { facetItems } from '@/lib/facetSelect'
import { browserTimeZone, isUtcZone } from '@/lib/goldWeekly'
import { parseSpan, type Span } from '@/lib/runsTable'
import { pipelineStatusView, runStatusView } from '@/lib/statusVocabulary'
import { formatRunTime } from '@/lib/syncRow'
import { parseDateMs } from '@/lib/utils'
import { buildUpcoming, formatNextAt, type LastRun, type UpcomingRow } from '@/lib/upcoming'

const SPAN_WORDS: Record<Span, string> = { '24h': '24 hours', '7d': '7 days' }
const HORIZON: Record<Span, 24 | 168> = { '24h': 24, '7d': 168 }
const WINDOW_OPTIONS = [
  ['24h', 'Next 24 hours'],
  ['7d', 'Next 7 days'],
] as const

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Insights › Coming up (UIFPE-681): the next expected run per table. Projected
 *  from each table's schedule in the browser; there is no scheduler to ask. */
export function Upcoming() {
  const [searchParams, setSearchParams] = useSearchParams()
  const span = parseSpan(searchParams.get('window'))
  const all = useAllRuns()
  const zone = useMemo(() => browserTimeZone(), [])
  const [find, setFind] = useState('')
  const [layers, setLayers] = useState<string[]>([])

  // A page left open keeps its "in 12 min" honest.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => new Date(), [tick, all.rows, all.pipelines, all.golds])

  const lastRuns = useMemo(() => {
    const map = new Map<string, LastRun & { ms: number }>()
    for (const row of all.rows) {
      const key = row.pipelineId ?? `gold:${row.goldId}`
      const ms = parseDateMs(row.startedAt)
      if (ms === null) continue
      const held = map.get(key)
      if (!held || ms > held.ms) map.set(key, { at: row.startedAt, status: row.status, ms })
    }
    return map
  }, [all.rows])

  const rows = useMemo(
    () => buildUpcoming(all.pipelines, all.golds, lastRuns, now, HORIZON[span]),
    [all.pipelines, all.golds, lastRuns, now, span],
  )

  // Find and Layer narrow the projection in the browser; neither is kept in the URL.
  const layerItems = useMemo(() => facetItems(rows.map((r) => r.layer), { selected: layers }), [rows, layers])
  const shown = useMemo(() => {
    const needle = find.trim().toLowerCase()
    return rows.filter(
      (r) => (!needle || r.object.toLowerCase().includes(needle)) && (layers.length === 0 || layers.includes(r.layer)),
    )
  }, [rows, find, layers])
  const narrowed = shown.length !== rows.length
  const golds = shown.filter((r) => r.key.startsWith('gold:')).length

  const setSpan = (next: Span) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        if (next === '24h') params.delete('window')
        else params.set('window', next)
        return params
      },
      { replace: true },
    )

  const clear = () => {
    setFind('')
    setLayers([])
  }

  const showLocal = !isUtcZone(zone)
  const columns: DataTableColumn<UpcomingRow>[] = [
    {
      key: 'next',
      header: 'Next expected',
      cell: (row) => {
        if (!row.nextAt) return '—'
        const at = formatNextAt(row.nextAt, zone, now)
        return (
          <div>
            <div className="tnum font-medium text-ink">
              {at.utc}
              {showLocal && <span className="text-ink-mute"> · {at.local}</span>}
            </div>
            <div className="text-meta text-ink-mute">{at.relative}</div>
          </div>
        )
      },
    },
    {
      key: 'object',
      header: 'Object',
      cell: (row) => (
        <Link to={row.href} className="font-semibold text-accent hover:underline">
          {row.object}
        </Link>
      ),
    },
    { key: 'layer', header: 'Layer', cell: (row) => row.layer },
    { key: 'cadence', header: 'Cadence', cell: (row) => row.cadence },
    {
      key: 'last',
      header: 'Last run',
      cell: (row) => (
        <div className="flex flex-wrap items-center gap-1.5">
          {row.lastRunAt ? (
            <>
              <span className="tnum">{formatRunTime(row.lastRunAt, now)}</span>
              <StatusBadge view={runStatusView(row.lastStatus)} size="sm" />
            </>
          ) : (
            <span className="text-ink-mute">—</span>
          )}
          {row.disabled && (
            <>
              <StatusBadge view={pipelineStatusView('DISABLED')} size="sm" />
              <span className="text-meta text-ink-mute">({row.note})</span>
            </>
          )}
        </div>
      ),
    },
    {
      key: 'action',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: (row) => <RowActionsCell open={{ to: row.href, name: row.object }} />,
    },
  ]

  const loading = all.loading || all.goldsLoading
  const empty =
    rows.length === 0 ? (
      <EmptyState icon={CalendarClock} title={`Nothing expected in the next ${SPAN_WORDS[span]}`} />
    ) : (
      <EmptyState
        icon={SearchX}
        title="No tables match"
        action={
          <Button type="button" variant="outline" size="sm" onClick={clear}>
            Clear all
          </Button>
        }
      >
        Clear the search or pick another layer.
      </EmptyState>
    )

  return (
    <div>
      <PageHeader title="Coming up" info="Next expected run per table, in your zone and UTC." />
      <Note tone="mute">
        Projected from each table&apos;s schedule, not read from the scheduler. Gold tables with no new silver
        changes skip by design. Reconcile checks run on demand and are not listed.
      </Note>
      {all.pendingPipelines > 0 && !loading && (
        <Note tone="mute">Last-run times are still loading for some syncs.</Note>
      )}
      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="upcoming-find">
            <ToolbarSearch id="upcoming-find" value={find} onChange={setFind} placeholder="Object" ariaLabel="Search objects" />
          </ToolbarField>
          <ToolbarField label="Window" htmlFor="upcoming-window">
            <WindowSelect
              id="upcoming-window"
              size="toolbar"
              label="Window"
              value={span}
              onChange={setSpan}
              options={WINDOW_OPTIONS}
            />
          </ToolbarField>
          <FacetSelect caption="Layer" noun="layers" items={layerItems} values={layers} onChange={setLayers} />
          <span className="ml-auto pb-2.5 text-meta text-ink-mute">times in your zone ({zone}) and UTC</span>
        </Toolbar>
        <DataTable
          ariaLabel="Upcoming runs"
          columns={columns}
          rows={shown}
          rowKey={(row) => row.key}
          loading={loading}
          empty={empty}
          framed={false}
        />
        {!loading && shown.length > 0 && (
          <CountLine>
            <span data-testid="upcoming-count">
              <CountSegment strong>{plural(shown.length, 'run', 'runs')} expected</CountSegment>
              {` in the next ${SPAN_WORDS[span]} · ${plural(shown.length - golds, 'sync', 'syncs')} · ${golds} gold`}
              {narrowed && ` · filtered from ${rows.length}`}
            </span>
          </CountLine>
        )}
      </ListCard>
      {all.collector}
    </div>
  )
}
