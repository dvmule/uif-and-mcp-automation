import { useFormatTemplates } from '@/hooks/useFormatTemplates'

export function Formats() {
  const q = useFormatTemplates()
  return (
    <div className="px-6 py-6">
      <h1 className="text-xl font-semibold text-slate-900 mb-4">Format templates</h1>
      {q.isLoading ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : !q.data?.length ? (
        <div className="text-sm text-slate-500">No format templates yet.</div>
      ) : (
        <ul className="divide-y border rounded-md bg-white">
          {q.data.map(f => (
            <li key={f.format_id} className="px-4 py-3 flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-slate-900">{f.name}</div>
                <div className="text-xs text-slate-500">{f.description ?? f.format_id}</div>
              </div>
              <span className="text-xs uppercase tracking-wide text-slate-600">{f.config.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
