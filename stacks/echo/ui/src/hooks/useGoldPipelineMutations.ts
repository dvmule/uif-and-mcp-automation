import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { goldCadenceLabel } from '@/lib/syncRow'
import { apiErrorMessage, isUndeployedRouteError } from '@/lib/apiError'
import type { GoldPipelineConfig, GoldScheduleChange } from '@/lib/types'

const GOLD_PIPELINES = ['gold-pipelines'] as const
const runLockKey = (goldId: string) => ['gold-run-lock', goldId] as const

/** PATCH …/toggle takes an EXPLICIT boolean and never flips, so two racing
 *  clicks converge on the last value sent rather than oscillating. */
export function useToggleGoldPipeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleGoldPipeline(id, enabled),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: GOLD_PIPELINES })
      qc.setQueryData<GoldPipelineConfig[]>(GOLD_PIPELINES, old =>
        old?.map(g => (g.gold_id === id ? { ...g, enabled } : g)),
      )
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: GOLD_PIPELINES })
    },
    onError: err => {
      void qc.invalidateQueries({ queryKey: GOLD_PIPELINES })
      toast.error(apiErrorMessage(err, 'Failed to update gold pipeline'))
    },
  })
}

/** POST …/run has no in-flight guard on the server (silver's 409 is never
 *  reached for gold). /gold-runs is deployed (dev and UAT since 2026-09-29); the
 *  session lock stays as the client-side guard: after a successful trigger the
 *  entity is locked for this session and a reload clears it. A server-side guard
 *  (UIFPE-516) is the durable fix. */
export function useTriggerGoldRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.triggerGoldRun(id),
    onSuccess: data => {
      qc.setQueryData(runLockKey(data.gold_id), data.run_id)
      void qc.invalidateQueries({ queryKey: GOLD_PIPELINES })
      toast.success(`Started ${data.kind} run for ${data.gold_id}`)
    },
    onError: err => {
      toast.error(apiErrorMessage(err, 'Failed to start gold run'))
    },
  })
}

/** The run id this session started for the entity, or null. Lives in the
 *  query cache so it survives list/grid switches and is reactive. */
export function useGoldRunLock(goldId: string): string | null {
  const { data } = useQuery<string | null>({
    queryKey: runLockKey(goldId),
    queryFn: () => null,
    enabled: false,
    initialData: null,
    staleTime: Infinity,
    gcTime: Infinity,
  })
  return data ?? null
}

export const SCHEDULE_NOT_DEPLOYED =
  'Changing the run time is not available in this environment yet'

/** API Gateway answers an unknown ROUTE with a bare 404 `{message}`; the
 *  handler answers an unknown ENTITY with a 404 that carries `error`. Only the
 *  first means "this endpoint has not been deployed here". */
export function scheduleErrorMessage(err: unknown): string {
  if (isUndeployedRouteError(err)) return SCHEDULE_NOT_DEPLOYED
  return apiErrorMessage(err, 'Failed to update run time')
}

export const CADENCE_NOT_APPLIED =
  'This environment saved the run time but not the cadence: its API predates ' +
  'console-editable cadence (UIFPE-564).'

/** PATCH …/schedule — the console-owned schedule (UIFPE-513, 532, 564):
 *  frequency, start_time and weekly_days on the weekly rung. Values are UTC;
 *  the dialog converted them from the viewer's zone.
 *
 *  The API half of UIFPE-564 rides the latched controller while this bundle
 *  deploys on merge, so a deployment can be one that ACCEPTS the request and
 *  ignores `frequency`. The response echoes the stored schedule: if the echo
 *  does not carry the cadence we asked for, the change is reported as not
 *  applied rather than cheerfully confirmed. */
export function useSetGoldSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, change }: { id: string; change: GoldScheduleChange }) => {
      const saved = await api.setGoldSchedule(id, change)
      if (change.frequency && saved?.schedule?.frequency !== change.frequency) {
        throw new Error(CADENCE_NOT_APPLIED)
      }
      return saved
    },
    onMutate: async ({ id, change }) => {
      await qc.cancelQueries({ queryKey: GOLD_PIPELINES })
      qc.setQueryData<GoldPipelineConfig[]>(GOLD_PIPELINES, old =>
        old?.map(g =>
          g.gold_id === id
            ? { ...g, schedule: { ...g.schedule,
                ...(change.start_time ? { start_time: change.start_time } : {}),
                ...(change.frequency ? { frequency: change.frequency } : {}),
                ...(change.weekly_days ? { weekly_days: change.weekly_days } : {}) } }
            : g,
        ),
      )
    },
    onSuccess: (_, { id, change }) => {
      void qc.invalidateQueries({ queryKey: GOLD_PIPELINES })
      const when = change.weekly_days
        ? `on ${change.weekly_days.join(', ')}${change.start_time ? ` at ${change.start_time} UTC` : ''}`
        : change.start_time ? `at ${change.start_time} UTC` : ''
      toast.success(change.frequency
        ? `${id} now runs ${goldCadenceLabel(change.frequency)}${when ? ` ${when}` : ''}`
        : `${id} now runs ${when}`)
    },
    onError: err => {
      void qc.invalidateQueries({ queryKey: GOLD_PIPELINES })
      toast.error(scheduleErrorMessage(err))
    },
  })
}
