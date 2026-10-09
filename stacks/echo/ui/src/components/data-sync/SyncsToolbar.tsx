import { useMemo, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, Eye, LayoutGrid, List, SlidersHorizontal } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { FacetSelect, Toolbar, ToolbarField, ToolbarSearch } from '@/components/relay'
import { FRESHNESS_OPTIONS, PIPELINE_STATUS_OPTIONS, facetItems } from '@/lib/facetSelect'
import { pipelineStatus } from '@/lib/filterMatchers'
import { parseFilters, serializeFilters, type SyncFilterState } from '@/lib/filters'
import { cn, freshness } from '@/lib/utils'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

export type ViewMode = 'list' | 'grid'
export type SortField =
  | 'table' | 'cloud' | 'status' | 'lastRun' | 'freshness' | 'schedule'
export interface SortState { field: SortField; dir: 'asc' | 'desc' }

export interface VisibleColumns {
  cloud: boolean
  layers: boolean
  schedule: boolean
  lastRun: boolean
  status: boolean
  freshness: boolean
  rows: boolean
}

interface Props {
  pipelines: PipelineConfig[]
  lastRunByPipeline: Map<string, RunMetrics | undefined>
  /** An entry exists only once that pipeline's runs have loaded. */
  runsByPipeline: Map<string, RunMetrics[]>

  search: string
  onSearchChange: (next: string) => void

  view: ViewMode
  onViewChange: (next: ViewMode) => void

  sort: SortState
  onSortChange: (next: SortState) => void

  visibleColumns: VisibleColumns
  onVisibleColumnsChange: (next: VisibleColumns) => void

  /** The inline TablePager at the right end. */
  pager?: ReactNode
}

// Table order (owner, 2026-09-30).
const COLUMN_LABELS: Record<keyof VisibleColumns, string> = {
  cloud:     'Cloud',
  layers:    'Layers',
  schedule:  'Schedule',
  lastRun:   'Last run',
  status:    'Status',
  freshness: 'Freshness',
  rows:      'Rows read → written',
}

const SORT_LABELS: Record<SortField, string> = {
  table:     'Table',
  cloud:     'Cloud',
  status:    'Status',
  lastRun:   'Last run',
  freshness: 'Freshness',
  schedule:  'Schedule',
}

const TOOL_BTN =
  'inline-flex h-[38px] items-center justify-center gap-1.5 rounded-[10px] border border-line bg-paper-card text-ink-soft transition-colors hover:border-paper-line hover:text-accent data-[state=open]:border-accent data-[state=open]:text-accent'

/** The Syncs card's toolbar: Find, one MultiSelect per URL facet, the view controls and the pager. */
export function SyncsToolbar({
  pipelines, lastRunByPipeline, runsByPipeline,
  search, onSearchChange,
  view, onViewChange,
  sort, onSortChange,
  visibleColumns, onVisibleColumnsChange,
  pager,
}: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const state = useMemo(() => parseFilters(searchParams), [searchParams])
  const set = (key: keyof SyncFilterState) => (values: string[]) =>
    setSearchParams(serializeFilters({ ...state, [key]: values } as SyncFilterState, searchParams), { replace: true })

  const items = useMemo(() => {
    // Status and freshness are judged only once a pipeline's runs have arrived, as the columns are.
    const judged = pipelines.filter(p => !p.enabled || runsByPipeline.has(p.pipeline_id))
    return {
      domain: facetItems(pipelines.map(p => p.metadata?.domain), { selected: state.domain }),
      source: facetItems(pipelines.map(p => p.metadata?.source_system), { selected: state.source }),
      status: facetItems(
        judged.map(p => pipelineStatus(p, lastRunByPipeline.get(p.pipeline_id), runsByPipeline.get(p.pipeline_id))),
        { vocab: PIPELINE_STATUS_OPTIONS, selected: state.status },
      ),
      freshness: facetItems(
        judged.map(p => freshness(p, lastRunByPipeline.get(p.pipeline_id)?.completed_at)),
        { vocab: FRESHNESS_OPTIONS, selected: state.freshness },
      ),
      tag: facetItems(pipelines.flatMap(p => p.metadata?.tags ?? []), { selected: state.tag }),
    }
  }, [pipelines, lastRunByPipeline, runsByPipeline, state])

  return (
    <Toolbar>
      <ToolbarField label="Find" htmlFor="syncs-find">
        <ToolbarSearch
          id="syncs-find"
          value={search}
          onChange={onSearchChange}
          placeholder="Table or sync"
          ariaLabel="Search table or sync"
        />
      </ToolbarField>
      <FacetSelect caption="Domain" noun="domains" items={items.domain} values={state.domain} onChange={set('domain')} />
      <FacetSelect caption="Source" noun="sources" items={items.source} values={state.source} onChange={set('source')} />
      <FacetSelect caption="Status" noun="statuses" items={items.status} values={state.status} onChange={set('status')} />
      <FacetSelect
        caption="Freshness"
        noun="freshness"
        items={items.freshness}
        values={state.freshness}
        onChange={set('freshness')}
      />
      <FacetSelect caption="Tags" noun="tags" items={items.tag} values={state.tag} onChange={set('tag')} />
      <ToolbarField label="View">
        <div className="flex items-center gap-1.5">
          <ViewToggle view={view} onChange={onViewChange} />
          {view === 'grid' && <SortPopover sort={sort} onChange={onSortChange} />}
          <ColumnsPopover visible={visibleColumns} onChange={onVisibleColumnsChange} />
        </div>
      </ToolbarField>
      {pager}
    </Toolbar>
  )
}

function ColumnsPopover({
  visible, onChange,
}: { visible: VisibleColumns; onChange: (next: VisibleColumns) => void }) {
  const toggle = (k: keyof VisibleColumns) =>
    onChange({ ...visible, [k]: !visible[k] })

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" aria-label="Toggle columns" className={cn(TOOL_BTN, 'w-[38px]')}>
          <Eye size={15} aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[240px] gap-0 rounded-card border border-line bg-paper-card p-3 shadow-card ring-foreground/10"
      >
        <p className="mb-2 text-[12px] font-semibold leading-[1.3] text-ink-soft">
          Columns
        </p>
        <div className="flex flex-col gap-0.5">
          {(Object.keys(COLUMN_LABELS) as Array<keyof VisibleColumns>).map(key => (
            <label
              key={key}
              className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1.5 text-[13px] text-ink transition-colors hover:bg-accent/[0.06]"
            >
              <input
                type="checkbox"
                checked={visible[key]}
                onChange={() => toggle(key)}
                className="h-3.5 w-3.5 cursor-pointer accent-accent"
              />
              <span className="flex-1">{COLUMN_LABELS[key]}</span>
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function SortPopover({
  sort, onChange,
}: { sort: SortState; onChange: (next: SortState) => void }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className={cn(TOOL_BTN, 'group px-3 text-[13px] font-semibold')}>
          <SlidersHorizontal size={14} aria-hidden="true" />
          <span>Sort: {SORT_LABELS[sort.field]} {sort.dir === 'asc' ? '↑' : '↓'}</span>
          <ChevronDown size={12} className="text-ink-mute transition-transform group-data-[state=open]:rotate-180" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[200px] gap-0 rounded-card border border-line bg-paper-card p-3 shadow-card ring-foreground/10"
      >
        <p className="mb-2 text-[12px] font-semibold leading-[1.3] text-ink-soft">
          Sort by
        </p>
        <div className="flex flex-col gap-0.5">
          {(Object.keys(SORT_LABELS) as SortField[]).map(field => {
            const selected = field === sort.field
            return (
              <button
                key={field}
                type="button"
                onClick={() => onChange({ field, dir: selected && sort.dir === 'asc' ? 'desc' : 'asc' })}
                className={cn(
                  'flex items-center justify-between rounded-md px-2 py-1.5 text-[13px] transition-colors',
                  selected ? 'bg-accent text-white' : 'text-ink hover:bg-accent/[0.06]',
                )}
              >
                <span>{SORT_LABELS[field]}</span>
                {selected && <span className="text-[11px]">{sort.dir === 'asc' ? 'A↑' : 'Z↓'}</span>}
              </button>
            )
          })}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function ViewToggle({
  view, onChange,
}: { view: ViewMode; onChange: (next: ViewMode) => void }) {
  return (
    <div className="inline-flex items-center overflow-hidden rounded-[10px] border border-line bg-paper-card">
      <ViewBtn label="List view" active={view === 'list'} onClick={() => onChange('list')}>
        <List size={14} aria-hidden="true" />
      </ViewBtn>
      <ViewBtn label="Grid view" active={view === 'grid'} onClick={() => onChange('grid')}>
        <LayoutGrid size={14} aria-hidden="true" />
      </ViewBtn>
    </div>
  )
}

function ViewBtn({
  active, onClick, label, children,
}: { active: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        'flex h-9 w-9 items-center justify-center transition-colors',
        active ? 'bg-accent-soft text-accent' : 'text-ink-soft hover:text-accent',
      )}
    >
      {children}
    </button>
  )
}
