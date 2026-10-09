import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'

interface Props {
  onClone: () => void
  onRawJson: () => void
  onResetWatermark: () => void
  onReBootstrap: () => void
  onDisable: () => void
  onDelete: () => void
  runInFlight?: boolean
}

export function SyncMoreMenu({
  onClone,
  onRawJson,
  onResetWatermark,
  onReBootstrap,
  onDisable,
  onDelete,
  runInFlight = false,
}: Props) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost">More</Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="min-w-[280px] bg-paper-card border border-paper-line rounded-lg p-1.5 shadow-lg"
      >
        <Group>
          <Item onClick={onClone} icon="⎘" title="Clone sync" sub="Open the wizard pre-filled" />
          <Item onClick={onRawJson} icon="{ }" title="View raw JSON" sub="Inspect the canonical config" />
        </Group>
        <Sep />
        <Group>
          <Item
            onClick={onResetWatermark}
            icon="↺"
            title="Reset watermark"
            sub="Re-extract from the beginning"
          />
          <Item
            onClick={onReBootstrap}
            icon="⇶"
            title="Re-bootstrap from scratch"
            sub="Drop Silver and re-run initial"
          />
        </Group>
        <Sep />
        <Group>
          <Item
            onClick={onDisable}
            icon="⏸"
            title="Disable sync"
            sub={runInFlight ? 'Unavailable while a run is in progress' : 'Pause scheduled runs'}
            disabled={runInFlight}
          />
          <Item
            onClick={onDelete}
            icon="⌫"
            title="Delete sync"
            sub={runInFlight ? 'Unavailable while a run is in progress' : 'Cascade delete config, data, grants'}
            danger
            disabled={runInFlight}
          />
        </Group>
      </PopoverContent>
    </Popover>
  )
}

function Group({ children }: { children: React.ReactNode }) {
  return <div>{children}</div>
}

function Sep() {
  return <div className="h-px bg-line my-1" />
}

function Item({
  onClick,
  icon,
  title,
  sub,
  danger,
  disabled,
}: {
  onClick: () => void
  icon: string
  title: string
  sub: string
  danger?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`grid grid-cols-[20px_1fr] gap-2.5 items-center px-2.5 py-2 rounded-md w-full text-left ${
        disabled
          ? 'opacity-40 cursor-not-allowed'
          : danger
            ? 'text-signal-fail hover:bg-accent-soft'
            : 'text-ink hover:bg-accent-soft'
      }`}
    >
      <span
        className={`text-center text-sm ${
          danger ? 'text-signal-fail' : 'text-accent'
        }`}
      >
        {icon}
      </span>
      <span>
        <span className="block text-[13px] font-medium">{title}</span>
        <span className="block text-[11px] text-accent">{sub}</span>
      </span>
    </button>
  )
}
