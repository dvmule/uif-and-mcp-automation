import { Info } from 'lucide-react'

interface Props {
  cloud: 'aws' | 'gcp'
}

// Placeholder until sample rows exist; the page supplies the card around it.
export function SampleRowsTable({ cloud }: Props) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-body">
        <Info size={16} className="text-ink-mute" />
        <span className="text-ink">Sample preview coming soon</span>
      </div>
      <p className="text-meta text-ink-mute">
        For now, use the <strong>Query</strong> tab to inspect rows in{' '}
        {cloud === 'aws' ? 'Athena' : 'BigQuery'}.
      </p>
    </div>
  )
}
