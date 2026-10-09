import { describe, it, expect } from 'vitest'
import { apiErrorMessage, isUndeployedRouteError } from '../apiError'

function axiosErr(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data },
  })
}

describe('isUndeployedRouteError', () => {
  // API Gateway answers an unknown ROUTE with a bare {message}; a handler
  // answers an unknown ENTITY with {error}. Only the first means "not deployed".
  it('is true only for a bare gateway 404', () => {
    expect(isUndeployedRouteError(axiosErr(404, { message: 'Not Found' }))).toBe(true)
    expect(isUndeployedRouteError(axiosErr(404, { error: "gold pipeline 'x' not found" }))).toBe(false)
    expect(isUndeployedRouteError(axiosErr(500, { message: 'boom' }))).toBe(false)
    expect(isUndeployedRouteError(new Error('offline'))).toBe(false)
    expect(isUndeployedRouteError(undefined)).toBe(false)
    expect(isUndeployedRouteError(null)).toBe(false)
  })
})

describe('apiErrorMessage', () => {
  it("prefers the server's error string, then the client message, then the fallback", () => {
    expect(apiErrorMessage(axiosErr(400, { error: 'bad start_time' }), 'fb')).toBe('bad start_time')
    expect(apiErrorMessage(axiosErr(502, {}), 'fb')).toBe('Request failed with status code 502')
    expect(apiErrorMessage('weird', 'fb')).toBe('fb')
  })
})
