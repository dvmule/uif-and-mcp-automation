import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getConfig, loadConfig, setConfigForTesting } from '@/services/config';

function mockFetch(response: Partial<Response> & { jsonBody?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: async () => response.jsonBody,
  } as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('loadConfig', () => {
  beforeEach(() => {
    setConfigForTesting(null);
    vi.unstubAllGlobals();
  });

  it('fetches /config.json and returns the API origin', async () => {
    const fetchMock = mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    const config = await loadConfig();
    expect(config.apiUrl).toBe('https://api.example.com');
    expect(fetchMock).toHaveBeenCalledWith('/config.json', { cache: 'no-store' });
  });

  it('requests config.json uncached, so a promotion is picked up', async () => {
    const fetchMock = mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    await loadConfig();
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: 'no-store' });
  });

  it('strips a trailing slash, because api-client appends /api/v1', async () => {
    mockFetch({ jsonBody: { apiUrl: 'https://api.example.com/' } });
    expect((await loadConfig()).apiUrl).toBe('https://api.example.com');
  });

  it('throws when config.json is missing', async () => {
    mockFetch({ ok: false, status: 404, jsonBody: undefined });
    await expect(loadConfig()).rejects.toThrow(/config\.json/);
  });

  it('throws when apiUrl is absent, rather than defaulting silently', async () => {
    mockFetch({ jsonBody: {} });
    await expect(loadConfig()).rejects.toThrow(/apiUrl/);
  });

  it('throws when apiUrl is the empty string', async () => {
    // The old failure mode: an empty origin built clean and issued same-origin
    // requests that CloudFront answered with index.html.
    mockFetch({ jsonBody: { apiUrl: '' } });
    await expect(loadConfig()).rejects.toThrow(/apiUrl/);
  });
});

describe('getConfig', () => {
  beforeEach(() => setConfigForTesting(null));

  it('throws if called before loadConfig resolved', () => {
    expect(() => getConfig()).toThrow(/loadConfig/);
  });

  it('returns the loaded config afterwards', async () => {
    mockFetch({ jsonBody: { apiUrl: 'https://api.example.com' } });
    await loadConfig();
    expect(getConfig().apiUrl).toBe('https://api.example.com');
  });
});
