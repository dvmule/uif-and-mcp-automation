import type { ReactNode } from 'react'
import { Tabs as TabsPrimitive } from 'radix-ui'
import { cn } from '@/lib/utils'

export interface TabCardTab {
  value: string
  label: ReactNode
  /** Shown in a bubble after the label. */
  count?: number
  content: ReactNode
}

export interface TabCardProps {
  tabs: TabCardTab[]
  defaultValue?: string
  /** Accessible name for the tab list. */
  label: string
  className?: string
}

/** A card whose header is Beacon's in-card tab strip; keyboard behaviour comes from Radix Tabs. */
export function TabCard({ tabs, defaultValue, label, className }: TabCardProps) {
  return (
    <TabsPrimitive.Root
      defaultValue={defaultValue ?? tabs[0]?.value}
      className={cn('mb-4 rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card', className)}
    >
      <TabsPrimitive.List aria-label={label} className="-mt-1 mb-4 flex gap-1 border-b border-line">
        {tabs.map((tab) => (
          <TabsPrimitive.Trigger
            key={tab.value}
            value={tab.value}
            className={cn(
              '-mb-px border-b-[3px] border-transparent px-3.5 py-2 text-body font-semibold text-ink-soft transition-colors hover:text-ink',
              'data-[state=active]:border-accent data-[state=active]:text-accent',
            )}
          >
            {tab.label}
            {tab.count != null && (
              <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 text-label font-semibold text-accent">
                {tab.count}
              </span>
            )}
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
