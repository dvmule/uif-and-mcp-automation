import { useState, useSyncExternalStore } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { PageTabsContext, TopLoader } from '@/components/relay'
import { isBusy, subscribe } from '@/lib/apiActivity'
import { browserStorage, readRailCollapsed, writeRailCollapsed } from '@/lib/railPreference'
import { AppHeader } from './AppHeader'
import { LabelledRail } from './LabelledRail'
import { pickRail, pickTabs } from './railConfig'

export function AppShell() {
  const { pathname } = useLocation()
  const busy = useSyncExternalStore(subscribe, isBusy)
  const [collapsed, setCollapsed] = useState(() => readRailCollapsed(browserStorage()))

  const toggleRail = () => {
    const next = !collapsed
    writeRailCollapsed(browserStorage(), next)
    setCollapsed(next)
  }

  return (
    <div className="relative isolate flex h-screen flex-col overflow-hidden bg-paper">
      <div className="echo-ground" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-brand focus:bg-paper-card focus:px-3 focus:py-2 focus:text-body focus:font-semibold focus:text-accent focus:shadow-card"
      >
        Skip to content
      </a>
      <AppHeader />
      <TopLoader active={busy} />
      <div className="flex min-h-0 flex-1">
        <LabelledRail config={pickRail(pathname)} collapsed={collapsed} onToggle={toggleRail} />
        <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col overflow-y-auto focus:outline-none">
          <div className="flex-1 p-6">
            <PageTabsContext.Provider value={pickTabs(pathname)}>
              <Outlet />
            </PageTabsContext.Provider>
          </div>
          <footer className="shrink-0 border-t border-line bg-paper-card px-6 py-2.5 text-center text-meta text-ink-mute">
            <a
              href="https://www.pearson.com/en-us/legal-information/terms-of-use.html"
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-accent hover:underline"
            >
              Terms
            </a>
            <span className="mx-2" aria-hidden="true">
              |
            </span>
            <a
              href="https://www.pearson.com/en-us/privacy-center/privacy-notices.html"
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-accent hover:underline"
            >
              Privacy
            </a>
            <div>
              Copyright © {new Date().getFullYear()} Pearson Education Inc. or its affiliate(s). All rights reserved.
            </div>
          </footer>
        </main>
      </div>
    </div>
  )
}
