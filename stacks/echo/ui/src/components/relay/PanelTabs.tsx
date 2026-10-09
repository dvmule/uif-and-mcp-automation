import type { ReactNode } from 'react'
import { Tabs as TabsPrimitive } from 'radix-ui'
import { cn } from '@/lib/utils'

export interface PanelTab {
  value: string
  label: ReactNode
  content: ReactNode
}

export interface PanelTabsProps {
  tabs: PanelTab[]
  defaultValue?: string
  /** Accessible name for the tab list. */
  label?: string
  className?: string
}

/** In-page underline tabs with the RouteTabs look, for panels that have no route of their own. */
export function PanelTabs({ tabs, defaultValue, label, className }: PanelTabsProps) {
  return (
    <TabsPrimitive.Root defaultValue={defaultValue ?? tabs[0]?.value} className={className}>
      <TabsPrimitive.List aria-label={label} className="mb-4 flex gap-1 border-b border-line">
        {tabs.map((tab) => (
          <TabsPrimitive.Trigger
            key={tab.value}
            value={tab.value}
            className={cn(
              '-mb-px border-b-2 border-transparent px-3 py-2 text-body font-medium text-ink-soft transition-colors hover:text-ink',
              'data-[state=active]:border-accent data-[state=active]:font-semibold data-[state=active]:text-accent',
            )}
          >
            {tab.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {tabs.map((tab) => (
        <TabsPrimitive.Content key={tab.value} value={tab.value} className="outline-none">
          {tab.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  )
}
