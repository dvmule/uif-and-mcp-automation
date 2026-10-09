// Routes deliberately mirror the UIF platform's scheme (/m/<module>/…) so
// this module can be lifted into that shell's route table unchanged.
//
// HashRouter + a relative asset base (vite base "./"), following the
// transform UI's pattern (UIFPE-99): the app deploys under a PREFIX of the
// shared RELAY CloudFront distribution (/m/beacon/) where no
// distribution-level SPA fallback exists for our paths — hash routes need
// none. The same bundle also works on the standalone sandbox distribution
// and in local dev.
import React, { Suspense } from "react";
import { HashRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import Shell from "./Shell.jsx";
import Wizard from "./Wizard.jsx";
import Health from "./Health.jsx";
import { Destinations, Exports, Overview, Registries, Roles } from "./screens.jsx";

// Split: the detail page carries the charting library, which is the bulk of
// the bundle and is only needed once someone opens an export.
const ExportDetail = React.lazy(() => import("./ExportDetail.jsx"));
// UIFPE-698: the export's insight page — same module, so no second chunk
// UIFPE-697: Insights in its own chunk, like the export pages
const Insights = React.lazy(() => import("./Insights.jsx"));
const ExportInsight = React.lazy(() => import("./ExportDetail.jsx").then((m) => ({ default: m.ExportInsight })));

const MODULE = "/m/beacon";

// UIFPE-708: one Wizard instance per export (keyed by jobId), so moving from
// one edit to another starts fresh instead of keeping the last one's state.
function EditWizard() {
  const { jobId } = useParams();
  return <Wizard key={jobId} />;
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Shell />}>
          {/* UIFPE-536: the module overview IS the landing screen — the old
              standalone Home dashboard is merged into it */}
          <Route index element={<Navigate to={MODULE} replace />} />
          <Route path="m/beacon" element={<Overview />} />
          <Route path="m/beacon/health" element={<Health />} />
          <Route path="m/beacon/exports" element={<Exports />} />
          {/* UIFPE-708: each route gets its own Wizard, so New Export after an
              edit opens fresh on its first step */}
          <Route path="m/beacon/exports/new" element={<Wizard key="new" />} />
          <Route path="m/beacon/exports/:jobId/edit" element={<EditWizard />} />
          <Route
            path="m/beacon/exports/:jobId"
            element={
              <Suspense fallback={<div className="text-[12.5px] text-ink-mute">Loading export…</div>}>
                <ExportDetail />
              </Suspense>
            }
          />
          <Route
            path="m/beacon/insights/:jobId"
            element={
              <Suspense fallback={<div className="text-[12.5px] text-ink-mute">Loading insight…</div>}>
                <ExportInsight />
              </Suspense>
            }
          />
          <Route path="m/beacon/destinations" element={<Destinations />} />
          <Route
            path="m/beacon/runs"
            element={
              <Suspense fallback={<div className="text-[12.5px] text-ink-mute">Loading insights…</div>}>
                <Insights />
              </Suspense>
            }
          />
          <Route path="settings/registries" element={<Registries />} />
          <Route path="settings/roles" element={<Roles />} />
          <Route path="settings" element={<Navigate to="/settings/registries" replace />} />
          {/* legacy in-app links from the pre-router build */}
          <Route path="exports" element={<Navigate to={`${MODULE}/exports`} replace />} />
          {/* UIFPE-536: Health moved under the Beacon menu — keep old bookmarks working */}
          <Route path="health" element={<Navigate to={`${MODULE}/health`} replace />} />
          <Route path="*" element={<Navigate to={MODULE} replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
