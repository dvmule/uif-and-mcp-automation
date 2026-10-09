import {
  ArrowLeft,
  Bell,
  BookOpen,
  Database,
  Globe,
  Home,
  LineChart,
  Plus,
  RefreshCw,
  Send,
  Shuffle,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import { BEACON_PANEL_PATH } from '@/lib/echoPaths'
import type { RouteTab } from '@/components/relay'

export interface RailLink {
  to: string
  label: string
  icon: LucideIcon
  /** Default matcher only: match `to` exactly, not its children. */
  end?: boolean
  /** Explicit active-state matcher; wins over the default. */
  match?: (pathname: string) => boolean
  /** Primary call-to-action pill. */
  quick?: boolean
  /** `to` is a sibling static app: render a plain <a> (full page load). */
  external?: boolean
}

export interface RailDivider {
  divider: true
}

export type RailEntry = RailLink | RailDivider

export interface RailConfig {
  heading: string
  items: RailEntry[]
}

/** True when `pathname` is `base` or a page under it. */
export function under(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`)
}

/** Drop trailing slashes so '/m/data-sync/syncs/' matches like '/m/data-sync/syncs'. */
export function normalizePath(pathname: string): string {
  return pathname.replace(/\/+$/, '') || '/'
}

const replicateMatch = (p: string) =>
  under(p, '/m/data-sync/syncs') ||
  p === '/m/data-sync/watermarks' ||
  p === '/m/data-sync/sources'

export const CONSOLE_RAIL: RailConfig = {
  heading: 'Echo',
  items: [
    { to: '/m/data-sync/syncs/new', label: 'New replication', icon: Plus, quick: true },
    { to: '/', label: 'Home', icon: Home, end: true },
    { to: '/m/data-sync/syncs', label: 'Replicate', icon: RefreshCw, match: replicateMatch },
    {
      to: '/m/data-sync/gold',
      label: 'Transform',
      icon: Shuffle,
      match: (p) => under(p, '/m/data-sync/gold'),
    },
    {
      to: '/m/data-sync',
      label: 'Insights',
      icon: LineChart,
      match: (p) =>
        p === '/m/data-sync' || under(p, '/m/data-sync/runs') || under(p, '/m/data-sync/upcoming'),
    },
    {
      to: '/m/data-quality/checks',
      label: 'Quality',
      icon: ShieldCheck,
      match: (p) => under(p, '/m/data-quality'),
    },
    {
      to: '/m/catalog/browse',
      label: 'Catalog',
      icon: BookOpen,
      match: (p) => under(p, '/m/catalog'),
    },
    { divider: true },
    { to: BEACON_PANEL_PATH, label: 'Export · Beacon', icon: Send, external: true },
  ],
}

export const SETTINGS_RAIL: RailConfig = {
  heading: 'Settings',
  items: [
    { to: '/', label: 'Back to console', icon: ArrowLeft, end: true },
    { divider: true },
    { to: '/settings/connections', label: 'Connections', icon: Database },
    { to: '/settings/notifications', label: 'Notifications', icon: Bell },
    { to: '/settings/environments', label: 'Environments', icon: Globe },
  ],
}

export const DATA_SYNC_TABS: RouteTab[] = [
  { to: '/m/data-sync/syncs', label: 'Syncs', end: true },
  { to: '/m/data-sync/watermarks', label: 'Watermarks', end: true },
  { to: '/m/data-sync/sources', label: 'Sources', end: true },
]

/** Insights (UIFPE-681): what has run, what runs next. */
export const INSIGHTS_TABS: RouteTab[] = [
  { to: '/m/data-sync', label: 'Overview', end: true },
  { to: '/m/data-sync/runs', label: 'Jobs', end: true },
  { to: '/m/data-sync/upcoming', label: 'Coming up', end: true },
]

export const DATA_QUALITY_TABS: RouteTab[] = [
  { to: '/m/data-quality/checks', label: 'Checks', end: true },
  { to: '/m/data-quality/runs', label: 'Runs', end: true },
]

/** The rail for a pathname: Settings has its own, everything else the console rail. */
export function pickRail(pathname: string): RailConfig {
  return pathname.startsWith('/settings') ? SETTINGS_RAIL : CONSOLE_RAIL
}

/**
 * Tabs for a pathname, or null when the page shows none. Detail, edit and
 * create pages own their header, so they get no tabs.
 */
export function pickTabs(pathname: string): RouteTab[] | null {
  const p = normalizePath(pathname)

  if (under(p, '/m/data-sync')) {
    if (p === '/m/data-sync' || under(p, '/m/data-sync/runs') || p === '/m/data-sync/upcoming') {
      return INSIGHTS_TABS
    }
    const replicatePage =
      p === '/m/data-sync/syncs' || p === '/m/data-sync/watermarks' || p === '/m/data-sync/sources'
    return replicatePage ? DATA_SYNC_TABS : null
  }

  if (under(p, '/m/data-quality')) {
    const onCheckPage = under(p, '/m/data-quality/checks') && p !== '/m/data-quality/checks'
    return onCheckPage ? null : DATA_QUALITY_TABS
  }

  return null
}
