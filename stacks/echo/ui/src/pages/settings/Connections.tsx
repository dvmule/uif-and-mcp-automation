import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Copy, Check, Database, SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  FacetSelect,
  ListCard,
  PageHeader,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { useConnections } from '@/hooks/useConnections'
import { useGcsConnections } from '@/hooks/useGcsConnections'
import { useSilverBqConnections } from '@/hooks/useSilverBqConnections'
import { usePipelines } from '@/hooks/usePipelines'
import { arnTail, connectionUsage, type ConnectionKind } from '@/lib/connections'
import { clearFacets, parseFacets, passesFacet, setFacet } from '@/lib/facetFilters'
import { facetItems, type FacetOption } from '@/lib/facetSelect'
import type { Connection } from '@/lib/types'

const FACET_KEYS = ['type'] as const
const TYPE_OPTIONS: FacetOption[] = [
  { key: 'oracle', label: 'Oracle' },
  { key: 'gcs', label: 'GCS' },
  { key: 'silver-bq', label: 'Silver → BigQuery' },
]
const TYPE_LABEL = Object.fromEntries(TYPE_OPTIONS.map((o) => [o.key, o.label])) as Record<ConnectionKind, string>
const KIND_ORDER: ConnectionKind[] = ['oracle', 'gcs', 'silver-bq']

interface ConnectionRow {
  kind: ConnectionKind
  connection: Connection
  used: number | null
}

// Copy the full ARN; the row shows only its tail.
function CopyArn({ arn }: { arn: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(arn)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <Button type="button" variant="ghost" size="xs" className="text-accent" onClick={copy} title="Copy ARN">
      {copied ? <Check className="text-signal-ok" aria-hidden="true" /> : <Copy aria-hidden="true" />}
      Copy ARN
    </Button>
  )
}

export function Connections() {
  const { data: oracleConns, isLoading: oracleLoading } = useConnections()
  const { data: gcsConns, isLoading: gcsLoading } = useGcsConnections()
  const { data: silverBqConns, isLoading: silverBqLoading } = useSilverBqConnections()
  const { data: pipelines } = usePipelines()
  const [showAdd, setShowAdd] = useState(false)
  const [search, setSearch] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const facets = useMemo(() => parseFacets(searchParams, FACET_KEYS), [searchParams])
  const isLoading = oracleLoading || gcsLoading || silverBqLoading

  // One row per secret across the three kinds, by kind order then name.
  const rows = useMemo(() => {
    const all = pipelines ?? []
    const byKind: Record<ConnectionKind, Connection[] | undefined> = {
      oracle: oracleConns,
      gcs: gcsConns,
      'silver-bq': silverBqConns,
    }
    return KIND_ORDER.flatMap((kind) =>
      [...(byKind[kind] ?? [])]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((connection): ConnectionRow => ({ kind, connection, used: connectionUsage(all, kind, connection.arn) })),
    )
  }, [oracleConns, gcsConns, silverBqConns, pipelines])

  const needle = search.trim().toLowerCase()
  const shown = rows.filter(
    (r) =>
      passesFacet(facets.type, r.kind) &&
      (!needle || `${r.connection.name} ${r.connection.arn}`.toLowerCase().includes(needle)),
  )
  const typeItems = useMemo(
    () => facetItems(rows.map((r) => r.kind), { vocab: TYPE_OPTIONS, selected: facets.type }),
    [rows, facets],
  )
  const ofKind = (kind: ConnectionKind) => shown.filter((r) => r.kind === kind).length
  const inUse = shown.filter((r) => (r.used ?? 0) > 0).length

  const clearAll = () => {
    setSearch('')
    setSearchParams(clearFacets(searchParams, FACET_KEYS), { replace: true })
  }

  const columns: DataTableColumn<ConnectionRow>[] = [
    {
      key: 'connection',
      header: 'Connection',
      cell: (r) => (
        <>
          <b className="font-semibold text-ink">{r.connection.name}</b>
          {r.connection.description && (
            <span className="block text-meta text-ink-mute">{r.connection.description}</span>
          )}
        </>
      ),
    },
    {
      key: 'secret',
      header: 'Secret',
      className: 'font-mono text-meta text-ink-soft',
      cell: (r) => <span title={r.connection.arn}>{arnTail(r.connection.arn)}</span>,
    },
    {
      key: 'type',
      header: 'Type',
      cell: (r) => (
        <span className="inline-flex items-center whitespace-nowrap rounded-full bg-accent-soft px-2 py-0 text-label font-bold text-accent">
          {TYPE_LABEL[r.kind]}
        </span>
      ),
    },
    {
      key: 'used',
      header: 'Used by',
      className: 'tnum',
      cell: (r) =>
        r.used === null ? <span className="text-ink-mute">—</span> : `${r.used} ${r.used === 1 ? 'sync' : 'syncs'}`,
    },
    {
      key: 'action',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: (r) => <CopyArn arn={r.connection.arn} />,
    },
  ]

  return (
    <div>
      <PageHeader
        title="Connections"
        info="Secrets the platform can read from. Adding one is a CLI step today."
        actions={
          <Button variant="outline" onClick={() => setShowAdd(true)}>
            How to add a connection
          </Button>
        }
      />

      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="connections-find">
            <ToolbarSearch
              id="connections-find"
              value={search}
              onChange={setSearch}
              placeholder="Name or ARN"
              ariaLabel="Search name or ARN"
            />
          </ToolbarField>
          <FacetSelect
            caption="Type"
            noun="types"
            items={typeItems}
            values={facets.type}
            onChange={(values) => setSearchParams(setFacet(searchParams, 'type', values), { replace: true })}
          />
        </Toolbar>

        <DataTable
          ariaLabel="Connections"
          columns={columns}
          rows={shown}
          rowKey={(r) => `${r.kind}:${r.connection.arn}`}
          loading={isLoading}
          framed={false}
          empty={
            rows.length === 0 ? (
              <EmptyState icon={Database} title="No connections registered">
                Add a credential to get started
              </EmptyState>
            ) : (
              <EmptyState
                icon={SearchX}
                title="No connections match these filters"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={clearAll}>
                    Clear all
                  </Button>
                }
              />
            )
          }
        />

        <CountLine>
          <CountSegment testid="connections-total" strong>
            <CountFigure loading={isLoading}>{shown.length}</CountFigure>{' '}
            {shown.length === 1 ? 'connection' : 'connections'}
          </CountSegment>
          {KIND_ORDER.map((kind) => (
            <span key={kind}>
              {' · '}
              <CountSegment testid={`connections-${kind}`}>
                <CountFigure loading={isLoading}>{ofKind(kind)}</CountFigure> {TYPE_LABEL[kind]}
              </CountSegment>
            </span>
          ))}
          {' · '}
          <CountSegment testid="connections-in-use">
            <CountFigure loading={isLoading}>{inUse}</CountFigure> in use
          </CountSegment>
          {shown.length !== rows.length && ` · filtered from ${rows.length}`}
        </CountLine>
      </ListCard>

      <Dialog open={showAdd} onOpenChange={setShowAdd}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>How to add a connection</DialogTitle>
          </DialogHeader>
          <div className="mt-2 space-y-4">
            <p className="text-body text-ink-mute">
              Credentials are registered via the CLI using <code className="rounded bg-accent-soft px-1 text-meta">register_secret.py</code>,
              which stores them in AWS Secrets Manager and tags them for discovery.
            </p>
            <div className="rounded-brand border border-line bg-paper-head p-3 text-body text-ink">
              <p className="font-semibold">CLI workflow</p>
              <code className="mt-1 block text-meta">
                python scripts/register_secret.py --name &lt;name&gt; --host &lt;host&gt; --port 1521 --service &lt;svc&gt; --user &lt;user&gt; --password &lt;pw&gt;
              </code>
            </div>
            <Button onClick={() => setShowAdd(false)} className="w-full">Close</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
