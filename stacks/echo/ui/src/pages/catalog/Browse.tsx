import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { AlertCircle, Database, SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  FacetSelect,
  ListCard,
  PageHeader,
  RowActionsCell,
  StatusBadge,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { LayerSelector } from '@/components/catalog/LayerSelector'
import { useCatalogDatasets } from '@/hooks/useCatalogDatasets'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { usePipelines } from '@/hooks/usePipelines'
import { api } from '@/lib/api'
import { catalogRow, type CatalogRow } from '@/lib/catalogRows'
import { getEnv } from '@/lib/envConfig'
import { clearFacets, parseFacets, passesFacet, setFacet } from '@/lib/facetFilters'
import { facetItems, type FacetOption } from '@/lib/facetSelect'
import { pickDisplayLastRun } from '@/lib/filterMatchers'
import { freshnessView } from '@/lib/statusVocabulary'
import type { RunMetrics } from '@/lib/types'

type Layer = 'bronze' | 'silver' | 'gold'

const VALID_LAYERS: Layer[] = ['bronze', 'silver', 'gold']
const FACET_KEYS = ['domain', 'cloud'] as const
const CLOUD_OPTIONS: FacetOption[] = [
  { key: 'aws', label: 'AWS' },
  { key: 'gcp', label: 'GCP' },
]

function parseLayer(value: string | null): Layer {
  if (value && (VALID_LAYERS as string[]).includes(value)) {
    return value as Layer
  }
  return 'silver'
}

/**
 * Catalog browse page. Layer lives in the URL as ?layer=silver per resolved
 * decision #4 — deep-linkable and refresh-safe. Domain and cloud filters are URL keys too.
 */
export function Browse() {
  const [searchParams, setSearchParams] = useSearchParams()
  const layer = parseLayer(searchParams.get('layer'))
  const [search, setSearch] = useState('')
  const facets = useMemo(() => parseFacets(searchParams, FACET_KEYS), [searchParams])

  const setLayer = (next: Layer) => {
    setSearchParams(prev => {
      const updated = new URLSearchParams(prev)
      updated.set('layer', next)
      return updated
    })
  }

  const { data: datasets, isLoading, isError, refetch } = useCatalogDatasets(layer)
  const { data: pipelines } = usePipelines()
  const { data: golds } = useGoldPipelines()

  // Runs the Syncs list already fetched, read from the shared cache: never fetched from here.
  const syncIds = useMemo(
    () => Array.from(new Set((datasets ?? []).map(d => d.sync_id).filter((id): id is string => !!id))),
    [datasets],
  )
  const cachedRuns = useQueries({
    queries: syncIds.map(id => ({
      queryKey: ['runs', id] as const,
      queryFn: () => api.getRuns(id),
      enabled: false,
    })),
  })
  const lastRunByPipeline = useMemo(() => {
    const m = new Map<string, RunMetrics | undefined>()
    syncIds.forEach((id, i) => {
      const data = cachedRuns[i]?.data as { items?: RunMetrics[] } | undefined
      if (data) m.set(id, pickDisplayLastRun(data.items ?? []))
    })
    return m
  }, [syncIds, cachedRuns])

  const rows = useMemo(
    () => (datasets ?? []).map(d => catalogRow(d, { pipelines, golds, lastRunByPipeline, env: getEnv() })),
    [datasets, pipelines, golds, lastRunByPipeline],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return rows.filter(r => {
      if (q && !`${r.dataset.database}.${r.dataset.table}`.includes(q)) return false
      return passesFacet(facets.domain, r.domain) && passesFacet(facets.cloud, r.dataset.cloud)
    })
  }, [rows, search, facets])

  const items = useMemo(
    () => ({
      domain: facetItems(rows.map(r => r.domain), { selected: facets.domain }),
      cloud: facetItems(rows.map(r => r.dataset.cloud), { vocab: CLOUD_OPTIONS, selected: facets.cloud }),
    }),
    [rows, facets],
  )
  const writeFacet = (key: (typeof FACET_KEYS)[number]) => (values: string[]) =>
    setSearchParams(setFacet(searchParams, key, values), { replace: true })

  const clearAll = () => {
    setSearch('')
    setSearchParams(clearFacets(searchParams, FACET_KEYS), { replace: true })
  }

  const columns: DataTableColumn<CatalogRow>[] = [
    {
      key: 'table',
      header: 'Table',
      cell: r => (
        <>
          <Link to={r.href} className="font-semibold text-accent hover:underline">
            {r.dataset.table}
          </Link>
          <span className="block font-mono text-meta text-ink-mute">{r.dataset.database}</span>
        </>
      ),
    },
    { key: 'domain', header: 'Domain', cell: r => r.domain },
    { key: 'cloud', header: 'Cloud', cell: r => r.dataset.cloud.toUpperCase() },
    {
      key: 'fed-by',
      header: 'Fed by',
      cell: r =>
        r.fedBy ? (
          <Link to={r.fedBy.href} className="font-semibold text-accent hover:underline">
            {r.fedBy.label}
          </Link>
        ) : (
          <span className="text-ink-mute">—</span>
        ),
    },
    {
      key: 'freshness',
      header: 'Freshness',
      cell: r =>
        r.freshness ? (
          <StatusBadge view={freshnessView(r.freshness, { neverRan: r.neverRan })} size="sm" />
        ) : (
          <span className="text-ink-mute">—</span>
        ),
    },
    {
      key: 'open',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: r => <RowActionsCell open={{ to: r.href, name: r.dataset.table }} />,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Catalog"
        info="Every table the platform writes, by layer."
        actions={<LayerSelector value={layer} onChange={setLayer} />}
      />

      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="catalog-find">
            <ToolbarSearch id="catalog-find" value={search} onChange={setSearch} placeholder="Table" ariaLabel="Search table" />
          </ToolbarField>
          <FacetSelect caption="Domain" noun="domains" items={items.domain} values={facets.domain} onChange={writeFacet('domain')} />
          <FacetSelect caption="Cloud" noun="clouds" items={items.cloud} values={facets.cloud} onChange={writeFacet('cloud')} />
        </Toolbar>

        <DataTable
          ariaLabel="Datasets"
          columns={columns}
          rows={filtered}
          rowKey={r => r.dataset.dataset}
          loading={isLoading}
          framed={false}
          empty={
            isError ? (
              <EmptyState
                icon={AlertCircle}
                title="Couldn't load datasets"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
                    Retry
                  </Button>
                }
              />
            ) : rows.length === 0 ? (
              <EmptyState icon={Database} title="No datasets in this layer" />
            ) : (
              <EmptyState
                icon={SearchX}
                title="No datasets match these filters"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={clearAll}>
                    Clear all
                  </Button>
                }
              />
            )
          }
        />

        {!isError && (
          <CountLine>
            <CountSegment testid="catalog-total" strong>
              <CountFigure loading={isLoading}>{filtered.length}</CountFigure> {filtered.length === 1 ? 'table' : 'tables'}
            </CountSegment>{' '}
            in {layer}
            {filtered.length !== rows.length && ` · filtered from ${rows.length}`}
          </CountLine>
        )}
      </ListCard>
    </div>
  )
}
