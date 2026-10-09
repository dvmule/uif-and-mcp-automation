import {
  createBrowserRouter,
  Navigate,
  type RouteObject,
} from 'react-router-dom'
import { RedirectLegacyPipelineDetail } from '@/components/RedirectLegacyPipelineDetail'
import { RedirectToBeaconPanel } from '@/components/RedirectToBeaconPanel'
import { AppShell } from '@/components/layout/AppShell'
import { RequireAuth } from '@/components/auth/RequireAuth'
import { Home } from '@/pages/Home'
import { Login } from '@/pages/Login'
import { NotFound } from '@/pages/NotFound'

// Data Sync module (Plan 3)
import { Overview as DataSyncOverview } from '@/pages/data-sync/Overview'

// Catalog module (Plan 4)
import { Browse as CatalogBrowse } from '@/pages/catalog/Browse'
import { DatasetDetail as CatalogDatasetDetail } from '@/pages/catalog/DatasetDetail'

// Settings module (Plan 5)
import { SettingsIndex } from '@/pages/settings/Index'
import { Connections as SettingsConnections } from '@/pages/settings/Connections'
import { Notifications as SettingsNotifications } from '@/pages/settings/Notifications'
import { Environments as SettingsEnvironments } from '@/pages/settings/Environments'
import { Syncs }          from '@/pages/data-sync/Syncs'
import { SyncDetail }     from '@/pages/data-sync/SyncDetail'
import { GoldList }       from '@/pages/data-sync/GoldList'
import { GoldDetail }     from '@/pages/data-sync/GoldDetail'
import { SyncDetailEdit } from '@/pages/data-sync/SyncDetailEdit'
import { NewSyncWizard }  from '@/pages/data-sync/NewSyncWizard'
import { Runs }           from '@/pages/data-sync/Runs'
import { Upcoming }       from '@/pages/data-sync/Upcoming'
import { Watermarks }     from '@/pages/data-sync/Watermarks'
import { Sources }        from '@/pages/data-sync/Sources'

// Data Quality module (Plan 6)
import { Checks as DqChecks }        from '@/pages/data-quality/Checks'
import { CheckDetail as DqDetail }   from '@/pages/data-quality/CheckDetail'
import { NewCheckWizard as DqWizard } from '@/pages/data-quality/NewCheckWizard'
import { Runs as DqRuns }            from '@/pages/data-quality/Runs'

// Partner Export module (Plan 7) — superseded by the Beacon panel (UIFPE-484):
// the entry routes below redirect there; the deeper routes stay until the
// module is retired.
import { Overview as PEOverview }            from '@/pages/partner-export/Overview'
import { ExportDetail as PEExportDetail }    from '@/pages/partner-export/ExportDetail'
import { Partners as PEPartners }            from '@/pages/partner-export/Partners'
import { PartnerDetail as PEPartnerDetail }  from '@/pages/partner-export/PartnerDetail'
import { Formats as PEFormats }              from '@/pages/partner-export/Formats'
import { Runs as PERuns }                    from '@/pages/partner-export/Runs'
import { NewExportWizard as PENewExportWizard } from '@/pages/partner-export/NewExportWizard'

export const partnerExportRoutes: RouteObject[] = [
  { path: 'm/partner-export',                element: <RedirectToBeaconPanel /> },
  { path: 'm/partner-export/overview',       element: <PEOverview /> },
  { path: 'm/partner-export/exports',        element: <RedirectToBeaconPanel /> },
  { path: 'm/partner-export/exports/new',    element: <PENewExportWizard /> },
  { path: 'm/partner-export/exports/:exportId',  element: <PEExportDetail /> },
  { path: 'm/partner-export/partners',       element: <PEPartners /> },
  { path: 'm/partner-export/partners/:partnerId', element: <PEPartnerDetail /> },
  { path: 'm/partner-export/formats',        element: <PEFormats /> },
  { path: 'm/partner-export/runs',           element: <PERuns /> },
]

export const routes: RouteObject[] = [
  // Interim sign-in, outside the shell (no sidebar, no header chrome of its own).
  { path: '/login', element: <Login /> },
  {
    path: '/',
    // One guard covers every child below, including the legacy redirects.
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Home /> },

      // Data Sync module — real pages
      { path: 'm/data-sync',                element: <DataSyncOverview /> },
      { path: 'm/data-sync/syncs',          element: <Syncs /> },
      { path: 'm/data-sync/syncs/new',      element: <NewSyncWizard /> },
      { path: 'm/data-sync/syncs/:id',      element: <SyncDetail /> },
      { path: 'm/data-sync/syncs/:id/edit/*', element: <SyncDetailEdit /> },
      { path: 'm/data-sync/gold',           element: <GoldList /> },
      { path: 'm/data-sync/gold/:goldId',   element: <GoldDetail /> },
      { path: 'm/data-sync/runs',           element: <Runs /> },
      { path: 'm/data-sync/upcoming',       element: <Upcoming /> },
      { path: 'm/data-sync/watermarks',     element: <Watermarks /> },
      { path: 'm/data-sync/sources',        element: <Sources /> },

      // Partner Export module (Plan 7)
      ...partnerExportRoutes,

      // Data Quality module (Plan 6)
      { path: 'm/data-quality',                element: <Navigate to="/m/data-quality/checks" replace /> },
      // Overview folded into the Checks stat strip (UIFPE-684).
      { path: 'm/data-quality/overview',       element: <Navigate to="/m/data-quality/checks" replace /> },
      { path: 'm/data-quality/checks',         element: <DqChecks /> },
      { path: 'm/data-quality/checks/new',     element: <DqWizard /> },
      { path: 'm/data-quality/checks/:checkId', element: <DqDetail /> },
      { path: 'm/data-quality/runs',           element: <DqRuns /> },
      // The Kinds catalogue lives in the New check wizard's first step (UIFPE-683).
      { path: 'm/data-quality/kinds',          element: <Navigate to="/m/data-quality/checks/new" replace /> },

      // Catalog module (Plan 4)
      { path: 'm/catalog', element: <Navigate to="/m/catalog/browse" replace /> },
      { path: 'm/catalog/browse', element: <CatalogBrowse /> },
      { path: 'm/catalog/datasets/:layer/:cloud/:database/:table', element: <CatalogDatasetDetail /> },

      // Settings module (Plan 5)
      { path: 'settings', element: <SettingsIndex /> },
      { path: 'settings/connections', element: <SettingsConnections /> },
      { path: 'settings/notifications', element: <SettingsNotifications /> },
      // UIFPE-543: the Alerts page became Notifications; keep the old address working.
      { path: 'settings/alerts', element: <Navigate to="/settings/notifications" replace /> },
      { path: 'settings/environments', element: <SettingsEnvironments /> },

      // Legacy redirects — kept for one release, removed after.
      { path: 'dashboard', element: <Navigate to="/" replace /> },
      { path: 'pipelines', element: <Navigate to="/m/data-sync/syncs" replace /> },
      { path: 'pipelines/new', element: <Navigate to="/m/data-sync/syncs/new" replace /> },
      { path: 'pipelines/:id', element: <RedirectLegacyPipelineDetail /> },
      { path: 'connections', element: <Navigate to="/settings/connections" replace /> },
      { path: 'reconciliation', element: <Navigate to="/m/data-quality/checks" replace /> },
      { path: 'alerts', element: <Navigate to="/settings/notifications" replace /> },
      { path: 'catalog', element: <Navigate to="/m/catalog/browse" replace /> },

      // Anything else: a page-not-found card inside the shell (last, so every route above wins).
      { path: '*', element: <NotFound /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
