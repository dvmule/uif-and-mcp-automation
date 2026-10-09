// Reads the New check wizard's ?subject= prefill: "<layer>.<database>.<table>", layer optional (silver).
import type { DqCheckSubject } from '@/lib/types'

const NAME = /^[A-Za-z0-9_]+$/

export function parseDqSubjectParam(raw: string | null | undefined): DqCheckSubject | null {
  const parts = (raw ?? '').trim().split('.')
  let layer: DqCheckSubject['layer'] = 'silver'
  if (parts.length === 3) {
    const first = parts.shift()!.toLowerCase()
    if (first !== 'silver' && first !== 'gold') return null
    layer = first
  }
  if (parts.length !== 2) return null
  const [database, table] = parts
  if (!NAME.test(database) || !NAME.test(table)) return null
  return { layer, database, table }
}
