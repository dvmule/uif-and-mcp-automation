import { useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { usePartnerExport, useRunPartnerExport, useDeletePartnerExport } from '@/hooks/usePartnerExports'
import { useExportRuns } from '@/hooks/useExportRuns'
import { DeliveryMethodBadge } from '@/components/partner-export/DeliveryMethodBadge'

type Tab = 'overview' | 'runs' | 'mapping' | 'delivery' | 'schedule'
const TABS: Tab[] = ['overview', 'runs', 'mapping', 'delivery', 'schedule']

export function ExportDetail() {
  const { exportId } = useParams<{ exportId: string }>()
  const exportQuery = usePartnerExport(exportId)
  const runsQuery = useExportRuns(exportId)
  const runMut = useRunPartnerExport()
  const delMut = useDeletePartnerExport()
  const [tab, setTab] = useState<Tab>('overview')

  if (exportQuery.isLoading) return <div className="p-6 text-sm text-slate-500">Loading…</div>
  if (!exportQuery.data) return <div className="p-6 text-sm text-red-600">Export not found.</div>

  const e = exportQuery.data
  return (
    <div className="px-6 py-6">
      <header className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{e.name}</h1>
          <div className="text-xs text-slate-500 mt-1">{e.source.layer} · {e.source.dataset}</div>
        </div>
        <div className="flex gap-2">
          <button type="button" disabled={runMut.isPending} onClick={() => exportId && runMut.mutate(exportId)} className="px-3 py-1.5 text-sm font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50">{runMut.isPending ? 'Triggering…' : 'Run now'}</button>
          <button type="button" disabled={delMut.isPending} onClick={() => exportId && window.confirm('Delete export?') && delMut.mutate(exportId)} className="px-3 py-1.5 text-sm font-medium border border-red-300 text-red-700 rounded-md hover:bg-red-50">Delete</button>
        </div>
      </header>

      <div role="tablist" className="flex gap-1 border-b border-slate-200 mb-4">
        {TABS.map(t => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-3 py-1.5 text-sm capitalize ${tab === t ? 'border-b-2 border-indigo-600 text-indigo-700 font-medium' : 'text-slate-600 hover:text-slate-900'}`}>{t}</button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === 'overview' && (
          <DefList rows={[
            ['Format', e.format.kind],
            ['Delivery', <DeliveryMethodBadge method={e.delivery.method} />],
            ['Path template', <code className="text-xs">{e.delivery.path_template}</code>],
            ['Schedule', `${e.schedule.frequency} ${e.schedule.run_at ?? ''}`],
            ['Health', e.health ?? 'unknown'],
          ]} />
        )}
        {tab === 'runs' && (
          <table className="w-full text-sm">
            <thead className="text-slate-500 text-left"><tr><th>Run</th><th>Status</th><th>Started</th><th>Rows</th></tr></thead>
            <tbody>
              {(runsQuery.data?.items ?? []).map(r => (
                <tr key={r.run_id} className="border-t">
                  <td className="font-mono text-xs">{r.run_id}</td>
                  <td>{r.status}</td>
                  <td>{r.started_at}</td>
                  <td>{r.rows_written ?? '—'}</td>
                </tr>
              ))}
              {(runsQuery.data?.items ?? []).length === 0 && (
                <tr><td colSpan={4} className="py-4 text-center text-slate-500">No runs yet.</td></tr>
              )}
            </tbody>
          </table>
        )}
        {tab === 'mapping' && (
          <table className="w-full text-sm">
            <thead className="text-slate-500 text-left"><tr><th>Source col</th><th>Output field</th><th>Type</th><th>Format</th></tr></thead>
            <tbody>
              {e.mapping.map((m, i) => (
                <tr key={i} className="border-t">
                  <td className="font-mono text-xs">{m.source_col}</td>
                  <td className="font-mono text-xs">{m.output_field}</td>
                  <td>{m.type}</td>
                  <td>{m.format ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === 'delivery' && (
          <DefList rows={[
            ['Method', <DeliveryMethodBadge method={e.delivery.method} />],
            ['Partner', e.delivery.partner_id],
            ['Connection', <code className="text-xs break-all">{e.delivery.connection_arn}</code>],
            ['Path template', <code className="text-xs">{e.delivery.path_template}</code>],
          ]} />
        )}
        {tab === 'schedule' && (
          <DefList rows={[
            ['Frequency', e.schedule.frequency],
            ['Run at', e.schedule.run_at ?? '—'],
            ['Watermark', e.schedule.watermark_window ?? '—'],
            ['Empty file', e.schedule.empty_file_behavior],
            ['Retention (days)', e.schedule.retention_days],
            ['Alerts', [
              e.schedule.alerts.on_failure && 'failure',
              e.schedule.alerts.on_partner_http_error && '4xx/5xx',
              e.schedule.alerts.on_zero_rows && 'zero-rows',
            ].filter(Boolean).join(' · ')],
          ]} />
        )}
      </div>
    </div>
  )
}

function DefList({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}
