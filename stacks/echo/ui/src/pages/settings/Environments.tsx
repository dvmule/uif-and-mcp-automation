import { CircleCheck, CircleDashed, Radio } from 'lucide-react'
import {
  CountLine,
  CountSegment,
  DataTable,
  ListCard,
  Note,
  PageHeader,
  StatusBadge,
  type DataTableColumn,
} from '@/components/relay'
import type { StatusView } from '@/lib/statusVocabulary'
import { getApiBaseUrl, getEnv, getEnvLabel } from '@/lib/envConfig'

interface EnvRow {
  env: string
  label: string
  api: string
  configured: boolean
}

const CURRENT: StatusView = { label: 'current', tone: 'live', icon: Radio, raw: 'current' }
const LIVE: StatusView = { label: 'Live', tone: 'ok', icon: CircleCheck, raw: 'live' }
const NOT_CONFIGURED: StatusView = { label: 'Not configured', tone: 'warn', icon: CircleDashed, raw: 'not configured' }

const COLUMNS: DataTableColumn<EnvRow>[] = [
  {
    key: 'env',
    header: 'Environment',
    cell: (r) => (
      <span className="inline-flex flex-wrap items-center gap-2">
        <b className="font-semibold text-ink">{r.label}</b>
        <span className="font-mono text-meta text-ink-mute">{r.env}</span>
        <StatusBadge view={CURRENT} size="sm" showIcon={false} />
      </span>
    ),
  },
  { key: 'api', header: 'API', className: 'font-mono text-meta text-ink-soft break-all', cell: (r) => r.api },
  {
    key: 'status',
    header: 'Status',
    cell: (r) => <StatusBadge view={r.configured ? LIVE : NOT_CONFIGURED} size="sm" />,
  },
]

/** The one environment this deployment's config.json binds it to. */
export function Environments() {
  const baseUrl = getApiBaseUrl()
  const row: EnvRow = {
    env: getEnv(),
    label: getEnvLabel(),
    api: baseUrl || '(apiUrl is empty in config.json)',
    configured: !!baseUrl,
  }

  return (
    <div>
      <PageHeader title="Environments" info="Where this console points. Read-only." />
      <ListCard>
        <DataTable ariaLabel="Environments" columns={COLUMNS} rows={[row]} rowKey={(r) => r.env} framed={false} />
        <CountLine>
          <CountSegment testid="environments-total" strong>
            1 environment
          </CountSegment>
        </CountLine>
      </ListCard>
      <Note>
        This UI deployment is bound to a single Echo environment by the config.json published alongside it.
        Each environment has its own URL — there is no in-app environment switcher.
      </Note>
    </div>
  )
}
