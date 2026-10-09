import { z } from 'zod'
import type { DqCheckKind } from '@/lib/types'

export const reconcileSchema = z.object({
  pk_cols: z.array(z.string()).min(1, 'at least one PK column'),
  pk_partition_col: z.string().optional(),
  max_deletes_per_run: z.coerce.number().int().positive(),
  dry_run: z.boolean(),
  oracle_secret_arn: z.string().regex(/^arn:aws:secretsmanager:/, 'must be a Secrets Manager ARN'),
  jdbc_url: z.string().startsWith('jdbc:'),
})

export const nullRateSchema = z.object({
  column: z.string().min(1),
  threshold_pct: z.coerce.number().min(0).max(1),
})

export const SCHEMAS: Partial<Record<DqCheckKind, z.ZodTypeAny>> = {
  reconcile:   reconcileSchema,
  'null-rate': nullRateSchema,
}

export const LIVE_KINDS = ['reconcile', 'null-rate'] as const
