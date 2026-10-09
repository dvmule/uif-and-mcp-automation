import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export const RUN_PARAM = 'run'

/** The open run drawer lives in the URL as `?run=<runId>`, so it deep-links and
 *  survives a reload. Every other query key is left alone. */
export function useRunDrawerParam(): {
  runId: string | null
  open: (runId: string) => void
  close: () => void
} {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get(RUN_PARAM)?.trim() || null

  const open = useCallback(
    (id: string) =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set(RUN_PARAM, id)
          return next
        },
        { replace: true },
      ),
    [setSearchParams],
  )

  const close = useCallback(
    () =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.delete(RUN_PARAM)
          return next
        },
        { replace: true },
      ),
    [setSearchParams],
  )

  return { runId, open, close }
}
