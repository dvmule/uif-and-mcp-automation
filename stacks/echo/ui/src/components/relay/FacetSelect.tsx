import type { MultiSelectItem } from '@/lib/multiSelect'
import { facetShown, facetUrlValues } from '@/lib/facetSelect'
import { MultiSelect } from './MultiSelect'
import { ToolbarField } from './Toolbar'

export interface FacetSelectProps {
  /** The bold caption, e.g. "Domain". */
  caption: string
  /** Plural lower-case noun, e.g. "domains". */
  noun: string
  items: MultiSelectItem[]
  /** The facet's values in the URL; none means every value is shown. */
  values: string[]
  onChange: (values: string[]) => void
}

/** A captioned toolbar MultiSelect over one URL facet. */
export function FacetSelect({ caption, noun, items, values, onChange }: FacetSelectProps) {
  return (
    <ToolbarField label={caption}>
      <MultiSelect
        size="sm"
        items={items}
        selected={facetShown(items, values)}
        onChange={(next) => onChange(facetUrlValues(items, values, next))}
        noun={noun}
        placeholder={`No ${noun}`}
        label={`Filter by ${caption.toLowerCase()}`}
      />
    </ToolbarField>
  )
}
