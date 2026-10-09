import {
  Home,
  RefreshCw,
  Send,
  ShieldCheck,
  BookOpen,
  Radio,
  FileInput,
  Upload,
  type LucideIcon,
} from 'lucide-react'

export type ModuleStatus = 'live' | 'soon'

export type Module = {
  id: string
  label: string
  icon: LucideIcon
  route: string
  status: ModuleStatus
  description?: string
}

export const MODULES: Module[] = [
  {
    id: 'home',
    label: 'Home',
    icon: Home,
    route: '/',
    status: 'live',
    description: 'Operations Dashboard',
  },
  {
    id: 'data-sync',
    label: 'Data Sync',
    icon: RefreshCw,
    route: '/m/data-sync',
    status: 'live',
    description: 'DB → Bronze / Silver / Gold',
  },
  {
    id: 'partner-export',
    label: 'Partner Export',
    icon: Send,
    route: '/m/partner-export',
    status: 'live',
    description: 'Gold → partner endpoints on schedule',
  },
  {
    id: 'data-quality',
    label: 'Data Quality',
    icon: ShieldCheck,
    route: '/m/data-quality',
    status: 'live',
    description: 'Reconcile · null-rate · freshness · drift',
  },
  {
    id: 'catalog',
    label: 'Catalog',
    icon: BookOpen,
    route: '/m/catalog',
    status: 'live',
    description: 'Browse Bronze / Silver / Gold with lineage',
  },
  {
    id: 'streaming',
    label: 'Streaming Ingest',
    icon: Radio,
    route: '/m/streaming',
    status: 'soon',
    description: 'CDC / Kafka → lake',
  },
  {
    id: 'file-ingest',
    label: 'File Ingest',
    icon: FileInput,
    route: '/m/file-ingest',
    status: 'soon',
    description: 'SFTP → lake',
  },
  {
    id: 'reverse-etl',
    label: 'Reverse ETL',
    icon: Upload,
    route: '/m/reverse-etl',
    status: 'soon',
    description: 'Lake → SaaS apps',
  },
]

export function getModuleByRoutePrefix(pathname: string): Module | undefined {
  if (pathname === '/' || pathname === '') {
    return MODULES.find((m) => m.id === 'home')
  }
  const moduleMatch = MODULES.find(
    (m) => m.id !== 'home' && pathname.startsWith(m.route),
  )
  if (moduleMatch) return moduleMatch
  // Settings and unknown paths fall back to home for chrome purposes.
  return MODULES.find((m) => m.id === 'home')
}
