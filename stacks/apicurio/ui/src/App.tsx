import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { ErrorBoundary } from '@/components/layout/ErrorBoundary';
import { SchemaListPage } from '@/pages/SchemaListPage';
import { SchemaDetailPage } from '@/pages/SchemaDetailPage';
import { ServiceListPage } from '@/pages/ServiceListPage';
import { ServicePortfolioPage } from '@/pages/ServicePortfolioPage';
import { PublishPage } from '@/pages/PublishPage';
import { SchemaEditPage } from '@/pages/SchemaEditPage';

export function App() {
  const { pathname } = useLocation();

  return (
    <AppShell>
      {/* Inside the shell, so a page that throws leaves the nav and the
        * header usable and the reader can navigate out of it. Keyed on the
        * path because a boundary does not reset itself: without the key, one
        * bad page would keep showing its error after you navigated away. */}
      <ErrorBoundary scope="page" key={pathname}>
        <Routes>
          <Route path="/" element={<Navigate to="/schemas" replace />} />
          <Route path="/schemas" element={<SchemaListPage />} />
          <Route path="/schemas/publish" element={<PublishPage />} />
          <Route path="/schemas/:groupId/:id" element={<SchemaDetailPage />} />
          {/* Declared after `:groupId/:id` for readability only -- react-router
              ranks routes by specificity, not by source order, so the static
              `edit` segment wins over the dynamic one either way. */}
          <Route path="/schemas/:groupId/:id/edit" element={<SchemaEditPage />} />
          <Route path="/services" element={<ServiceListPage />} />
          <Route path="/services/:urn" element={<ServicePortfolioPage />} />
          {/* Legacy route: Non-compliant collapsed into a /schemas tab. */}
          {/* /twin and /drift were removed with the RDF graph (ADR-0007). */}
          {/* /deploy and /delivery-lab removed with their rail entries. */}
          <Route path="/non-compliant" element={<Navigate to="/schemas?view=non-compliant" replace />} />
          {/* Catch-all. Without it an unmatched path renders the shell with an
              empty content area, which reads as a broken page rather than a bad
              URL -- exactly what /deploy and /delivery-lab did once their routes
              were removed. */}
          <Route path="*" element={<Navigate to="/schemas" replace />} />
        </Routes>
      </ErrorBoundary>
    </AppShell>
  );
}
