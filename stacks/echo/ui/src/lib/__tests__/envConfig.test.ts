import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CONFIG_URL,
  __setRuntimeConfigForTests,
  getApiBaseUrl,
  getEnv,
  getEnvLabel,
  getRuntimeConfig,
  isRuntimeConfigLoaded,
  loadRuntimeConfig,
  showSandbox,
} from '@/lib/envConfig'

const res = (status: number, body: unknown, jsonThrows = false) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (jsonThrows) throw new SyntaxError('Unexpected token < in JSON')
      return body
    },
  }) as Response

// src/test/setup.ts seeds a config for every test, because the accessors throw
// when nothing is loaded and most suites render environment-dependent UI. This
// suite is the one that tests the guard itself, so it clears the seed. Vitest
// runs this beforeEach after the setup file's, which is what makes the override
// work -- the same arrangement the auth suites use.
beforeEach(() => {
  __setRuntimeConfigForTests(null)
})

afterEach(() => {
  // Leave nothing loaded for the next test, whichever file it is in.
  __setRuntimeConfigForTests(null)
  vi.restoreAllMocks()
})

describe('loadRuntimeConfig', () => {
  it('loads a valid config and exposes it through the accessors', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      res(200, { apiUrl: 'https://api.uat.invalid', env: 'uat' })
    )

    const cfg = await loadRuntimeConfig(fetchImpl as unknown as typeof fetch)

    expect(cfg).toEqual({ apiUrl: 'https://api.uat.invalid', env: 'uat' })
    expect(getApiBaseUrl()).toBe('https://api.uat.invalid')
    expect(getEnv()).toBe('uat')
    expect(getEnvLabel()).toBe('UAT')
    expect(isRuntimeConfigLoaded()).toBe(true)
  })

  // config.json is unhashed and its name never changes, so a copy cached from a
  // previous deployment would point this bundle at another environment's API.
  it('fetches with cache: no-store', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      res(200, { apiUrl: 'https://api.dev.invalid', env: 'dev' })
    )

    await loadRuntimeConfig(fetchImpl as unknown as typeof fetch)

    expect(fetchImpl).toHaveBeenCalledWith(CONFIG_URL, { cache: 'no-store' })
  })

  it('trims surrounding whitespace in apiUrl', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      res(200, { apiUrl: '  https://api.dev.invalid  ', env: 'dev' })
    )

    const cfg = await loadRuntimeConfig(fetchImpl as unknown as typeof fetch)

    expect(cfg.apiUrl).toBe('https://api.dev.invalid')
  })

  describe('refusals', () => {
    const cases: Array<[string, unknown]> = [
      ['no apiUrl key', { env: 'dev' }],
      ['an empty apiUrl', { apiUrl: '', env: 'dev' }],
      ['a whitespace-only apiUrl', { apiUrl: '   ', env: 'dev' }],
      ['a non-string apiUrl', { apiUrl: 42, env: 'dev' }],
    ]

    it.each(cases)('refuses %s', async (_label, body) => {
      const fetchImpl = vi.fn().mockResolvedValue(res(200, body))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/has no "apiUrl"/)
      expect(isRuntimeConfigLoaded()).toBe(false)
    })

    // Defaulting an unknown env to dev is how a prod deployment ends up
    // labelled DEV and showing the non-production badge.
    it.each([
      ['an unknown env', { apiUrl: 'https://x.invalid', env: 'staging' }],
      ['a missing env', { apiUrl: 'https://x.invalid' }],
      ['an empty env', { apiUrl: 'https://x.invalid', env: '' }],
    ])('refuses %s rather than defaulting to dev', async (_label, body) => {
      const fetchImpl = vi.fn().mockResolvedValue(res(200, body))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/is not one of dev, uat, prod/)
    })

    it('refuses a JSON body that is not an object', async () => {
      const fetchImpl = vi.fn().mockResolvedValue(res(200, ['nope']))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/has no "apiUrl"/)
    })

    it('refuses null', async () => {
      const fetchImpl = vi.fn().mockResolvedValue(res(200, null))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/is not a JSON object/)
    })

    // A bundle synced without config.json gets index.html back from
    // CloudFront, which is a 200 carrying HTML -- hence the JSON-parse case
    // below as well as the 404.
    it('refuses a non-2xx response and names the status', async () => {
      const fetchImpl = vi.fn().mockResolvedValue(res(404, {}))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/returned HTTP 404/)
    })

    it('refuses a body that is not valid JSON', async () => {
      const fetchImpl = vi.fn().mockResolvedValue(res(200, null, true))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/is not valid JSON/)
    })

    it('reports a network failure rather than propagating it raw', async () => {
      const fetchImpl = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
      await expect(
        loadRuntimeConfig(fetchImpl as unknown as typeof fetch)
      ).rejects.toThrow(/could not fetch \/config\.json/)
    })
  })
})

describe('reading before loading', () => {
  // The alternative -- an empty base URL -- surfaces as a CORS error or a
  // request to the UI's own origin, a long way from the cause.
  it.each([
    ['getRuntimeConfig', getRuntimeConfig],
    ['getApiBaseUrl', getApiBaseUrl],
    ['getEnv', getEnv],
    ['getEnvLabel', getEnvLabel],
    ['showSandbox', showSandbox],
  ])('%s throws', (_label, fn) => {
    expect(() => fn()).toThrow(/read before it was loaded/)
  })

  it('isRuntimeConfigLoaded reports false without throwing', () => {
    expect(isRuntimeConfigLoaded()).toBe(false)
  })
})

describe('showSandbox', () => {
  it.each([
    ['dev', true],
    ['uat', true],
    ['prod', false],
  ] as const)('is %s -> %s', (env, expected) => {
    __setRuntimeConfigForTests({ apiUrl: 'https://x.invalid', env })
    expect(showSandbox()).toBe(expected)
  })
})
