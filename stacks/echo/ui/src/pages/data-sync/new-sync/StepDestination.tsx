// Step 2: which cloud this sync writes bronze and silver to.
import { ChoiceCard } from '@/components/wizard/ChoiceCard'
import { useWizardStore } from '@/store/wizardStore'
import { FieldHint } from './fields'

const OPTIONS: { cloud: 'aws' | 'gcp'; title: string; description: string }[] = [
  {
    cloud: 'aws',
    title: 'AWS Cloud',
    description: 'Bronze on S3 + Silver Iceberg with Glue catalog. Default for most syncs.',
  },
  {
    cloud: 'gcp',
    title: 'GCP Cloud',
    description: 'Bronze on GCS + Silver Iceberg with BigLake. Use when downstream consumers are in GCP.',
  },
]

export function StepDestination() {
  const { destinationCloud, patch } = useWizardStore()

  return (
    <div className="space-y-3">
      <FieldHint>
        Choose where this sync writes Bronze and Silver. The wizard adapts later steps based on this choice.
      </FieldHint>
      <div className="grid gap-3 md:grid-cols-2">
        {OPTIONS.map(opt => (
          <ChoiceCard
            key={opt.cloud}
            selected={destinationCloud === opt.cloud}
            onSelect={() => patch({ destinationCloud: opt.cloud })}
            title={opt.title}
          >
            <span className="mt-0.5 block text-meta text-ink-mute">{opt.description}</span>
          </ChoiceCard>
        ))}
      </div>
    </div>
  )
}
