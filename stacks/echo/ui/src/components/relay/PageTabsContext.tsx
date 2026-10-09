import { createContext, useContext } from 'react'
import type { RouteTab } from './RouteTabs'

/** Section tabs for the current page, supplied by the shell; null where a page has none. */
export const PageTabsContext = createContext<RouteTab[] | null>(null)

export function usePageTabs(): RouteTab[] | null {
  return useContext(PageTabsContext)
}
