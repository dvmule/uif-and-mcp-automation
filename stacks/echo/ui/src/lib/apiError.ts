import axios from 'axios'

/** API Gateway answers an unknown ROUTE with a bare 404 `{message}`; a handler
 *  answers an unknown ENTITY with a 404 that carries `error`. Only the first
 *  means "this endpoint has not been deployed here". */
export function isUndeployedRouteError(err: unknown): boolean {
  if (!axios.isAxiosError(err) || err.response?.status !== 404) return false
  const data = err.response.data as { error?: string } | undefined
  return !data?.error
}

/** The server's `error` string when there is one, else the client-side message. */
export function apiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const body = err.response?.data as { error?: string } | undefined
    return body?.error ?? err.message
  }
  return err instanceof Error ? err.message : fallback
}
