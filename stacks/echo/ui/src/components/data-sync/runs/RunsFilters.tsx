import { useMemo } from 'react'
import { FacetSelect, SegmentedControl, Toolbar, ToolbarField, ToolbarSearch } from '@/components/relay'
import { facetItems, type FacetOption } from '@/lib/facetSelect'
import {
  RUN_LAYER_LABEL,
  statusBucket,
  type RunLayer,
  type RunRow,
  type RunsFilter,
  type RunsView,
} from '@/lib/runsTable'

export interface ObjectOption {
  /** pipeline_id or gold_id: what the URL carries. */
  id: string
  label: string
}

interface Props {
  filter: RunsFilter
  onChange: (next: RunsFilter) => void
  objects: ObjectOption[]
  /** Every loaded run, to count each option. */
  rows?: RunRow[]
}

const VIEWS: Array<{ value: RunsView; label: string }> = [
  { value: 'runs', label: 'Runs' },
  { value: 'objects', label: 'Objects' },
]

const STATUSES: FacetOption[] = [
  { key: 'SUCCEEDED', label: 'Succeeded' },
  { key: 'WARNING', label: 'Warning' },
  { key: 'FAILED', label: 'Failed' },
  { key: 'RUNNING', label: 'Running' },
  { key: 'SKIPPED', label: 'Skipped' },
]

const LAYERS: FacetOption[] = (['bronze', 'silver', 'gold', 'reconcile'] as RunLayer[]).map((l) => ({
  key: l,
  label: RUN_LAYER_LABEL[l],
}))

/** The Jobs card's toolbar: Find, the Status, Layer and Object facets, and the Runs | Objects view.
 *  The state itself lives in the URL; this only edits a RunsFilter. */
export function RunsFilters({ filter, onChange, objects, rows = [] }: Props) {
  const items = useMemo(
    () => ({
      status: facetItems(rows.map((r) => statusBucket(r.status)), { vocab: STATUSES, selected: filter.status }),
      layer: facetItems(rows.map((r) => r.layer), {
        vocab: LAYERS,
        selected: filter.layer,
        label: (k) => RUN_LAYER_LABEL[k as RunLayer] ?? k,
      }),
      object: facetItems(rows.map((r) => r.pipelineId ?? r.goldId), {
        vocab: objects.map((o) => ({ key: o.id, label: o.label })),
        selected: filter.object,
      }),
    }),
    [rows, objects, filter.status, filter.layer, filter.object],
  )

  return (
    <Toolbar>
      <ToolbarField label="Find" htmlFor="jobs-find">
        <ToolbarSearch
          id="jobs-find"
          value={filter.search}
          onChange={(search) => onChange({ ...filter, search })}
          placeholder="Object or run id"
          ariaLabel="Search object or run id"
        />
      </ToolbarField>
      <FacetSelect
        caption="Status"
        noun="statuses"
        items={items.status}
        values={filter.status}
        onChange={(status) => onChange({ ...filter, status })}
      />
      <FacetSelect
        caption="Layer"
        noun="layers"
        items={items.layer}
        values={filter.layer}
        onChange={(layer) => onChange({ ...filter, layer: layer as RunLayer[] })}
      />
      <FacetSelect
        caption="Object"
        noun="objects"
        items={items.object}
        values={filter.object}
        onChange={(object) => onChange({ ...filter, object })}
      />
      <ToolbarField label="View">
        <SegmentedControl
          size="sm"
          label="View"
          options={VIEWS}
          value={filter.view}
          onChange={(view) => onChange({ ...filter, view })}
        />
      </ToolbarField>
    </Toolbar>
  )
}
