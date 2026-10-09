import { Link, useParams } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Kv, ObjectHeader, Pill, TabCard } from '@/components/relay'
import { LineageCard } from '@/components/catalog/LineageCard'
import { SchemaTable } from '@/components/catalog/SchemaTable'
import { SampleRowsTable } from '@/components/catalog/SampleRowsTable'
import { useDatasetSchema } from '@/hooks/useDatasetSchema'
import { useLineage } from '@/hooks/useLineage'
import { useCatalogDatasets } from '@/hooks/useCatalogDatasets'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { usePipelines } from '@/hooks/usePipelines'
import { catalogRow } from '@/lib/catalogRows'
import { getEnv } from '@/lib/envConfig'
import { goldReadersOfSilver } from '@/lib/goldDetail'

type Layer = 'bronze' | 'silver' | 'gold'
type Cloud = 'aws' | 'gcp'

const LAYER_LABEL: Record<Layer, string> = { bronze: 'Bronze', silver: 'Silver', gold: 'Gold' }
// Bronze lands Parquet; silver and gold are Iceberg tables.
const FORMAT: Record<Layer, string> = { bronze: 'Parquet', silver: 'Iceberg', gold: 'Iceberg' }

function athenaUrl(database: string, table: string) {
  const sql = encodeURIComponent(`SELECT * FROM ${database}.${table} LIMIT 100;`)
  return `https://eu-west-1.console.aws.amazon.com/athena/home?region=eu-west-1#/query-editor?query=${sql}`
}

function bigQueryUrl(database: string, table: string) {
  return `https://console.cloud.google.com/bigquery?p=&d=${database}&t=${table}&page=table`
}

/**
 * Catalog dataset detail page.
 * Route: /m/catalog/datasets/:layer/:cloud/:database/:table (4-part per
 * resolved decision #3).
 */
export function DatasetDetail() {
  const params = useParams<{ layer: Layer; cloud: Cloud; database: string; table: string }>()
  const valid = !!(params.layer && params.cloud && params.database && params.table)
  const { layer = 'silver', cloud = 'aws', database = '', table = '' } = params
  const dsRef = `${layer}.${cloud}.${database}.${table}`

  // Find this dataset's sync_id by inspecting the layer-scoped list.
  const { data: datasetsForLayer } = useCatalogDatasets(layer)
  const { data: pipelines } = usePipelines()
  const { data: golds } = useGoldPipelines()
  const { data: schema, isLoading: schemaLoading } = useDatasetSchema(layer, database, table)
  const { data: lineage, isLoading: lineageLoading } = useLineage(valid ? dsRef : undefined)

  if (!valid) {
    return <p className="text-body text-ink-mute">Invalid dataset path.</p>
  }

  const datasetRow = (datasetsForLayer ?? []).find(d => d.dataset === dsRef)
  const row = datasetRow ? catalogRow(datasetRow, { pipelines, golds, env: getEnv() }) : null
  const readers = layer === 'silver' ? goldReadersOfSilver(golds, `${database}.${table}`, getEnv()) : []
  // Database, layer, cloud and format are pills; the meta line keeps the rest.
  const meta = [
    row?.fedBy && `fed by ${row.fedBy.label}`,
    readers.length > 0 && `read by ${readers.join(', ')}`,
  ].filter(Boolean).join(' · ')

  const overview = (
    <dl>
      <Kv label="Dataset"><span className="break-all font-mono">{dsRef}</span></Kv>
      <Kv label="Layer"><span className="font-mono">{layer}</span></Kv>
      <Kv label="Cloud"><span className="font-mono">{cloud}</span></Kv>
      <Kv label="Database"><span className="break-all font-mono">{database}</span></Kv>
      <Kv label="Table"><span className="break-all font-mono">{table}</span></Kv>
      {datasetRow?.sync_id && (
        <Kv label="Produced by sync">
          <Link to={`/m/data-sync/syncs/${datasetRow.sync_id}`} className="font-mono text-accent hover:underline">
            {datasetRow.sync_id}
          </Link>
        </Kv>
      )}
    </dl>
  )

  const lineageTab =
    lineageLoading || !lineage ? (
      <Skeleton className="h-40 rounded-card" />
    ) : (
      <div className="space-y-3">
        {lineage.upstream && (
          <p className="text-label font-semibold uppercase tracking-wider text-ink-mute">Produced by sync</p>
        )}
        <LineageCard
          upstream={lineage.upstream}
          downstream_exports={lineage.downstream_exports}
          downstream_checks={lineage.downstream_checks}
        />
      </div>
    )

  const query = (
    <div className="space-y-3">
      <p className="text-body text-ink">Open this dataset in {cloud === 'gcp' ? 'BigQuery' : 'Athena'}:</p>
      <a
        href={cloud === 'gcp' ? bigQueryUrl(database, table) : athenaUrl(database, table)}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-2 text-body font-semibold text-accent hover:underline"
      >
        <ExternalLink size={14} />
        {cloud === 'gcp' ? 'Open in BigQuery' : 'Open in Athena'}
      </a>
    </div>
  )

  return (
    <div className="mx-auto max-w-[1180px]">
      <ObjectHeader
        back={{ to: `/m/catalog/browse?layer=${layer}`, label: `Catalog · ${LAYER_LABEL[layer] ?? layer}` }}
        title={table}
        pills={
          <>
            <Pill tone="mute">{database}</Pill>
            <Pill tone="accent">{layer}</Pill>
            <Pill tone="accent">{cloud.toUpperCase()}</Pill>
            <Pill tone="mute">{FORMAT[layer]}</Pill>
          </>
        }
        meta={meta || undefined}
        actions={
          row?.fedBy && (
            <Button asChild variant="outline">
              <Link to={row.fedBy.href}>{layer === 'gold' ? 'Open gold table' : 'Open sync'}</Link>
            </Button>
          )
        }
      />

      <TabCard
        label="Dataset sections"
        tabs={[
          { value: 'overview', label: 'Overview', content: overview },
          {
            value: 'schema',
            label: 'Schema',
            count: schema?.columns?.length,
            content: <SchemaTable columns={schema?.columns} isLoading={schemaLoading} />,
          },
          { value: 'sample', label: 'Sample', content: <SampleRowsTable cloud={cloud} /> },
          { value: 'lineage', label: 'Lineage', content: lineageTab },
          { value: 'query', label: 'Query', content: query },
        ]}
      />
    </div>
  )
}
