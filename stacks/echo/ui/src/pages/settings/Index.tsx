import { Database, Bell, Globe } from 'lucide-react'
import { ListCard, PageHeader } from '@/components/relay'
import { SettingsTile } from '@/components/settings/SettingsTile'

// The three live sections only; Users, API tokens and Audit have no page yet (UIFPE-684).
const TILES = [
  {
    icon: Database,
    label: 'Connections',
    description: 'Secrets the platform can read from',
    to: '/settings/connections',
  },
  {
    icon: Bell,
    label: 'Notifications',
    description: 'Who is emailed when a gold table\'s alarm fires',
    to: '/settings/notifications',
  },
  {
    icon: Globe,
    label: 'Environments',
    description: 'Where this console points',
    to: '/settings/environments',
  },
] as const

export function SettingsIndex() {
  return (
    <div>
      <PageHeader
        title="Settings"
        info="Platform-wide configuration. Workflow areas are on the left rail."
      />
      {/* Clipped so a hovered first or last row keeps the card's corners. */}
      <ListCard className="overflow-hidden">
        {TILES.map(t => (
          <SettingsTile key={t.label} icon={t.icon} label={t.label} description={t.description} to={t.to} />
        ))}
      </ListCard>
    </div>
  )
}
