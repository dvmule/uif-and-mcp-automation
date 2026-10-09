import { cn } from '@/lib/utils'

type Layer = 'bronze' | 'silver' | 'gold'

interface Props {
  value: Layer
  onChange: (layer: Layer) => void
}

const LAYERS: Array<{ key: Layer; label: string }> = [
  { key: 'bronze', label: 'Bronze' },
  { key: 'silver', label: 'Silver' },
  { key: 'gold', label: 'Gold' },
]

export function LayerSelector({ value, onChange }: Props) {
  return (
    <div
      role="tablist"
      aria-label="Layer"
      className="inline-flex rounded-md border border-paper-line bg-paper p-0.5"
    >
      {LAYERS.map(layer => {
        const active = layer.key === value
        return (
          <button
            key={layer.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(layer.key)}
            className={cn(
              'px-3 py-1 text-sm rounded transition-colors',
              active
                ? 'bg-paper-card text-ink shadow-sm font-medium'
                : 'text-ink-mute hover:text-ink',
            )}
          >
            {layer.label}
          </button>
        )
      })}
    </div>
  )
}
