// Contract tests — the load-bearing test in UIF. Every response from the running
// API is validated against the registered openapi.yaml (via its JSON Schema
// 2020-12 component schemas). If code and contract drift, this fails the build.
import { describe, expect, it } from 'vitest';
import request from 'supertest';
import { createApp } from '../../src/app.js';
import { contractHasPath, schemaErrors } from './contract-spec.js';

const AUTH = { Authorization: 'Bearer test-token' } as const;
const app = () => createApp();

describe('contract surface', () => {
  it('declares the paths the implementation serves', () => {
    expect(contractHasPath('/developers')).toBe(true);
    expect(contractHasPath('/developers/{developerId}')).toBe(true);
  });
});

describe('POST /api/v1/developers', () => {
  it('registers a developer and the 201 body matches Developer', async () => {
    const res = await request(app())
      .post('/api/v1/developers')
      .set(AUTH)
      .send({ email: 'ada@example.com', displayName: 'Ada Lovelace' });

    expect(res.status).toBe(201);
    expect(res.headers.location).toMatch(/\/api\/v1\/developers\/dev_/);
    expect(schemaErrors('Developer', res.body)).toEqual([]);
  });

  it('returns a 400 problem+json on invalid input', async () => {
    const res = await request(app())
      .post('/api/v1/developers')
      .set(AUTH)
      .send({ email: 'nope', displayName: '' });

    expect(res.status).toBe(400);
    expect(res.type).toBe('application/problem+json');
    expect(schemaErrors('Problem', res.body)).toEqual([]);
  });

  it('returns a 409 problem+json when the email is taken', async () => {
    const server = app();
    const body = { email: 'dup@example.com', displayName: 'Dup' };
    await request(server).post('/api/v1/developers').set(AUTH).send(body);
    const res = await request(server).post('/api/v1/developers').set(AUTH).send(body);

    expect(res.status).toBe(409);
    expect(schemaErrors('Problem', res.body)).toEqual([]);
  });

  it('returns 401 problem+json without a bearer token', async () => {
    const res = await request(app())
      .post('/api/v1/developers')
      .send({ email: 'ada@example.com', displayName: 'Ada' });

    expect(res.status).toBe(401);
    expect(schemaErrors('Problem', res.body)).toEqual([]);
  });
});

describe('GET /api/v1/developers', () => {
  it('returns a page matching DeveloperPage', async () => {
    const res = await request(app()).get('/api/v1/developers').set(AUTH);
    expect(res.status).toBe(200);
    expect(schemaErrors('DeveloperPage', res.body)).toEqual([]);
  });
});

describe('GET /api/v1/developers/{developerId}', () => {
  it('returns 404 problem+json for an unknown id', async () => {
    const res = await request(app()).get('/api/v1/developers/dev_missing').set(AUTH);
    expect(res.status).toBe(404);
    expect(schemaErrors('Problem', res.body)).toEqual([]);
  });

  it('returns the developer after registration', async () => {
    const server = app();
    const created = await request(server)
      .post('/api/v1/developers')
      .set(AUTH)
      .send({ email: 'grace@example.com', displayName: 'Grace Hopper' });
    const res = await request(server).get(`/api/v1/developers/${created.body.id}`).set(AUTH);

    expect(res.status).toBe(200);
    expect(res.body.email).toBe('grace@example.com');
    expect(schemaErrors('Developer', res.body)).toEqual([]);
  });
});
