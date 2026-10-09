import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { loadRuntimeConfig, CONFIG_URL } from '@/lib/envConfig'
import { router } from './router'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

const root = createRoot(document.getElementById('root')!)

/**
 * A boot failure is rendered, not swallowed.
 *
 * Without this the app would mount with no API origin and every request would
 * go to the UI's own CloudFront distribution, which answers with index.html --
 * so the first symptom is a JSON parse error or a CORS complaint in the
 * console, a long way from "config.json was never published". Saying so on the
 * page is the difference between a five-minute fix and an afternoon.
 *
 * Deliberately plain DOM: the design system, the router and the query client
 * all sit above this in the dependency order, and a failure screen that needs
 * them can fail for a second reason while reporting the first.
 */
function renderBootFailure(message: string): void {
  root.render(
    <div
      role="alert"
      style={{
        fontFamily: 'ui-sans-serif, system-ui, sans-serif',
        maxWidth: '44rem',
        margin: '4rem auto',
        padding: '0 1rem',
        color: '#0D004D',
      }}
    >
      <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>
        Echo could not start
      </h1>
      <p style={{ marginTop: '0.75rem' }}>
        This deployment&rsquo;s runtime configuration could not be loaded, so
        the UI has no API endpoint to call.
      </p>
      <pre
        style={{
          marginTop: '1rem',
          padding: '0.75rem',
          background: '#EDECF6',
          borderRadius: '0.375rem',
          whiteSpace: 'pre-wrap',
          fontSize: '0.8125rem',
        }}
      >
        {message}
      </pre>
      <p style={{ marginTop: '1rem', fontSize: '0.875rem' }}>
        {CONFIG_URL} is written per environment by the publish step, from that
        environment&rsquo;s <code>api_endpoint</code> output. If the bundle was
        synced before the API Gateway existed, or the CloudFront invalidation
        did not include {CONFIG_URL}, re-run the publish.
      </p>
    </div>
  )
}

loadRuntimeConfig()
  .then(() => {
    root.render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <RouterProvider router={router} />
            <Toaster />
          </TooltipProvider>
        </QueryClientProvider>
      </StrictMode>
    )
  })
  .catch((err: unknown) => {
    const message = err instanceof Error ? err.message : String(err)
    // Logged as well as rendered: the page text is for whoever opened the UI,
    // the console entry is for whoever is looking at a screenshot of it.
    console.error('Echo boot failed:', err)
    renderBootFailure(message)
  })
