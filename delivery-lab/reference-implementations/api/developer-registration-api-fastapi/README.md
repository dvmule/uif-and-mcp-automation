# product-developer-registration-api (FastAPI) — the worked API reference, in Python

This is the **Python/FastAPI** twin of `../developer-registration-api/` (TypeScript/Express). Both implement the **same registered [`openapi.yaml`](openapi.yaml)** and every rule in `../../../capabilities/api/guidelines.md`. Two stacks, one standard — the point is that UIF's API discipline (naming, contract, testing, security, observability) is **language-agnostic**: a domain picks the stack it wants and still ships the same contract, the same RFC 7807 errors, the same tests, and one container image.

It is a *teaching artifact*, not a domain. A real feature ships inside `domains/product/` (see `../../../capabilities/docs/capability-domain-jira-map.md`).

## API First with FastAPI
FastAPI can generate an OpenAPI document from code, but UIF is **contract-first**: the hand-written [`openapi.yaml`](openapi.yaml) is the source of truth, registered before implementation. The FastAPI app serves it exactly, and the contract test validates the running app against it — so the code conforms to the contract, not the other way round.

## The four things it demonstrates

### 1. Naming (guidelines §1)
`product-developer-registration-api`, base path `/api/v1`, plural-noun resources, `camelCase` fields, RFC 9110 status codes (`201`+`Location` on create, `400/401/404/409/500`). Identical to the Express twin.

### 2. Contract / YAML documentation (guidelines §2)
[`openapi.yaml`](openapi.yaml) — OpenAPI 3.1, one shared RFC 7807 `Problem` schema, bearer `securityScheme` in the contract, an example per response. Pydantic models in [`app/domain.py`](app/domain.py) mirror the contract's schemas (`extra="forbid"` = `additionalProperties: false`). Render human docs from the YAML (e.g. Redocly), never hand-write them.

### 3. Testing → automation → build (guidelines §3)
```
pytest tests/unit         # pure domain logic (Pydantic validation), no network
pytest tests/contract     # running app validated against openapi.yaml (the load-bearing test)
pytest                    # both, with coverage floor (fail-under 80)
```
- **Unit** ([`tests/unit`](tests/unit)) targets `app/domain.py` directly.
- **Contract** ([`tests/contract`](tests/contract)) drives the app with `TestClient` and validates every response against the contract's own component schemas using `jsonschema` (Draft 2020-12) — the Python analogue of the Express example's Ajv 2020 helper ([`contract_spec.py`](tests/contract/contract_spec.py)). Because OpenAPI 3.1 ⊂ JSON Schema 2020-12, there is no second copy to drift.

### 4. Standards & guidelines built in (guidelines §4)
- **Security by default:** `require_bearer` dependency rejects any request without a bearer token (`401` problem+json). A real deployment verifies the JWT against the OIDC issuer.
- **Observability by default:** an HTTP middleware emits one structured JSON log line per request with the RED signals and a propagated `x-correlation-id`.
- **Uniform errors:** `RequestValidationError` → `400` problem+json; unhandled → `500` problem+json — never a `200` with an error body.
- **Idempotency & pagination:** the contract accepts `Idempotency-Key` on `POST`; `GET /developers` is cursor-based and bounded (`pageSize` 1–100).

## Run it locally
```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8080

curl -sX POST http://localhost:8080/api/v1/developers \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","displayName":"Ada Lovelace"}'
```

## Build the image (the one build/deploy contract)
```
docker build -t product-developer-registration-api-fastapi .
docker run -p 8080:8080 product-developer-registration-api-fastapi
```
The [`Dockerfile`](Dockerfile) produces a small, non-root runtime image — the artifact CD promotes (`../../../capabilities/delivery/`).

## Layout
```
developer-registration-api-fastapi/
├── openapi.yaml          # the contract, registered first (§2) — same as the Express twin
├── app/
│   ├── domain.py         # Pydantic models + helper (unit-tested)
│   ├── store.py          # in-memory repo (swap for a DB behind the same interface)
│   ├── problem.py        # RFC 7807 problem+json
│   ├── middleware.py     # security + observability (§4)
│   └── main.py           # FastAPI app factory — serves the contract exactly
├── tests/
│   ├── unit/             # §3 unit
│   └── contract/         # §3 contract vs openapi.yaml
├── Dockerfile            # §3 the one image
└── pyproject.toml        # deps + pytest coverage floor + ruff
```

## The CI gate this maps to (guidelines §3)
```
lint (ruff) → unit → contract (vs registered schema) → build container → security scan → publish rendered docs
```
See the repo-root workflow in `../../../.github/workflows/` and the API standard in `../../../capabilities/api/guidelines.md`.
