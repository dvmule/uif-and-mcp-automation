import { useMemo } from 'react'
import { usePartnerExports } from '@/hooks/usePartnerExports'
import { useAllExportRuns } from '@/hooks/useExportRuns'

function KPI({ label, value, tone, testId }: { label: string; value: string | number; tone?: string; testId: string }) {
  return (
    <div className="px-4 py-3 bg-white border rounded-md flex-1">
      <div className="text-xs text-slate-500">{label}</div>
      <div data-testid={testId} className={`text-2xl font-semibold ${tone ?? 'text-slate-900'}`}>
        {value}
      </div>
    </div>
  )
}

export function Overview() {
  const exportsQ = usePartnerExports()
  const runsQ = useAllExportRuns()
  const stats = useMemo(() => {
    const exports = exportsQ.data ?? []
    const runs = runsQ.data?.items ?? []
    const active = exports.filter(e => e.enabled).length
    const pass = runs.filter(r => r.status === 'SUCCEEDED').length
    const fail = runs.filter(r => r.status === 'FAILED').length
    const total = pass + fail
    const onTimeRate = total === 0 ? null : Math.round((pass / total) * 100)
    return { active, pass, fail, onTimeRate }
  }, [exportsQ.data, runsQ.data])

  return (
    <div className="px-6 py-6">
      <h1 className="text-xl font-semibold text-slate-900 mb-4">Partner Export overview</h1>
      <div className="flex gap-4">
        <KPI label="Active exports" value={stats.active} testId="kpi-active" />
        <KPI label="Passed (last 24h)" value={stats.pass} tone="text-emerald-700" testId="kpi-pass" />
        <KPI label="Failed (last 24h)" value={stats.fail} tone="text-red-600" testId="kpi-fail" />
        <KPI label="On-time rate" value={stats.onTimeRate == null ? '—' : `${stats.onTimeRate}%`} testId="kpi-ontime" />
      </div>
    </div>
  )
}
