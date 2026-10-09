import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from '@/App';
import { ErrorBoundary } from '@/components/layout/ErrorBoundary';
import { loadConfig } from '@/services/config';
import '@/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

const rootElement = document.getElementById('root')!;

function renderApp() {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      {/* Outside the router, because the shell itself can throw — its nav
          counts run their own queries — and the boundary inside `App` sits
          below it and cannot catch that. */}
      <ErrorBoundary scope="app">
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </ErrorBoundary>
    </React.StrictMode>,
  );
}

// The config has to be in hand before the first request, so it is awaited
// before the first render rather than raced with it. Failing visibly here is
// deliberate: the previous behaviour was an empty API origin that rendered a
// working-looking dashboard and then showed an empty list, which reads as "no
// schemas" rather than "misconfigured deploy".
loadConfig()
  .then(renderApp)
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    rootElement.textContent = `Configuration error: ${message}`;
  });
