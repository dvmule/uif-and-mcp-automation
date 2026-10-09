# product-developer-registration-api — the worked API reference

This is the runnable companion to `../../../capabilities/api/guidelines.md`. It implements **every rule** in that standard so a Forward Deployed Engineer can read one small service end-to-end and see how UIF builds an API: how it is **named**, how its contract is **documented in YAML**, how it is **tested from unit through contract**, and how it is **built** into the one container image — all on globally recognised standards (OpenAPI 3.1, JSON Schema 2020-12, RFC 9110, RFC 7807, SemVer 2.0.0, OAuth 2.1/OIDC).

It is a *teaching artifact*, not a domain. A real feature ships inside `domains/product/` (see `../../../capabilities/docs/capability-domain-jira-map.md`); this reference is what a domain copies the pattern from.

> **Same contract, another stack.** For the Python twin of this reference — the identical `openapi.yaml` implemented in FastAPI — see `../developer-registration-api-fastapi/`. The UIF API standard is language-agnostic.

## The four things it demonstrates

### 1. Naming (guidelines §1)
- **API:** `product-developer-registration-api` — `<domain>-<resource>-api`, kebab-case, no version in the name.
- **Base path:** `/api/v1` — major version in the URL only.
- **Resources:** plural nouns, the HTTP method is the verb — `POST /api/v1/developers`, `GET /api/v1/developers/{developerId}`. Never `/createDeveloper`.
- **Fields:** `camelCase`, ISO 8601 UTC timestamps (`createdAt`), opaque string ids (`dev_…`).
- **Status codes (RFC 9110):** `201`+`Location` on create, `200` on read, `400/401/404/409` client errors, `500` server error — never `200` with an error body.

### 2. Contract / YAML documentation (guidelines §2)
[`openapi.yaml`](openapi.yaml) is the single source of truth, written **before** the code. Every operation carries an `operationId`, `summary`, `description`, `tags`, a typed `requestBody`, and every response — including a `4xx` and the `5xx` — each with a realistic `example`. Errors use one shared RFC 7807 `Problem` schema (`application/problem+json`). The bearer security scheme is declared in the contract, so auth is part of the contract, not an afterthought.

Render human docs from the YAML (never hand-write them):
```
npm run docs      # → dist/docs/index.html via Redocly
```

### 3. Testing → automation → build (guidelines §3)
The test pyramid, each layer automated:
```
npm run test:unit       # pure domain logic — validation, no network
npm run test:contract   # running API validated against openapi.yaml (the load-bearing test)
npm test                # both, with coverage thresholds enforced
```
- **Unit** ([`tests/unit`](tests/unit)) targets `src/domain.ts` directly.
- **Contract** ([`tests/contract`](tests/contract)) drives the running app with `supertest` and validates every response against the **registered** `openapi.yaml`. Because OpenAPI 3.1 is a strict superset of JSON Schema 2020-12, the test compiles the contract's own component schemas with Ajv 2020 ([`contract-spec.ts`](tests/contract/contract-spec.ts)) — the very schemas the registry validates, so there is no second copy to drift. A response that doesn't match its declared schema fails the build.

### 4. Standards & guidelines built in (guidelines §4)
- **Security by default:** no unauthenticated endpoint — `requireBearer` rejects any request without a bearer token (`401` problem+json). A real deployment verifies the JWT against the OIDC issuer.
- **Observability by default:** one structured JSON log line per request with the RED signals (status, duration) and a propagated `x-correlation-id`.
- **Idempotency:** the contract accepts an `Idempotency-Key` header on `POST` for safe retries.
- **Pagination:** `GET /developers` is cursor-based and bounded (`pageSize` 1–100), never an unbounded list.

## Run it locally
```
npm install
npm run dev            # http://localhost:8080

# register a developer
curl -sX POST http://localhost:8080/api/v1/developers \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","displayName":"Ada Lovelace"}'
```

## Build the image (the one build/deploy contract)
```
docker build -t product-developer-registration-api .
docker run -p 8080:8080 product-developer-registration-api
```
The multi-stage [`Dockerfile`](Dockerfile) produces a small, non-root runtime image. That image — not the source — is what CD promotes (`../../../capabilities/delivery/`).

## Layout
```
developer-registration-api/
├── openapi.yaml          # the contract, registered first (§2)
├── src/
│   ├── domain.ts         # pure validation + rules (unit-tested)
│   ├── store.ts          # in-memory repo (swap for a DB behind the same interface)
│   ├── problem.ts        # RFC 7807 problem+json
│   ├── middleware.ts     # security + observability (§4)
│   ├── app.ts            # Express wiring — serves the contract exactly
│   └── server.ts         # bootstrap
├── tests/
│   ├── unit/             # §3 unit
│   └── contract/         # §3 contract vs openapi.yaml
├── Dockerfile            # §3 the one image
├── vitest.config.ts      # coverage floor
└── package.json
```

## The CI gate this maps to (guidelines §3)
```
lint → unit → contract (vs registered schema) → build container → security scan → publish rendered docs
```
See the repo-root workflow in `../../../.github/workflows/` and the API standard in `../../../capabilities/api/guidelines.md`.
