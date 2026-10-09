# API — Guidelines (the worked standard)

This is the detailed, do-this companion to the API capability `README.md`. It tells a Forward Deployed Engineer exactly how to name an API, document its contract in YAML, test it from unit through automation, and build it — using globally recognised standards so the result is identifiable and readable to any engineer, inside or outside UIF. Read it once; then follow the runnable reference at `../../delivery-lab/reference-implementations/api/developer-registration-api/`, which implements every rule below.

This document is also the **template shape** for every other capability's `guidelines.md` (Event, Schema, MCP, A2A, Agent, …): same five sections — Naming, Contract/YAML documentation, Testing → automation → build, Standards & guidelines, Definition of done — so the whole fabric reads the same way.

Grounding standards (all public, so anyone can verify): OpenAPI 3.1, JSON Schema 2020-12, RFC 9110 (HTTP semantics), RFC 7807 (Problem Details), RFC 8594 (Sunset header), Semantic Versioning 2.0.0, OAuth 2.1 / OpenID Connect, and the Apicurio Registry (open-source, the tool behind `../schema/`'s Schema Registry First principle, §2).

---

## 1. Naming

Naming is the first thing a consumer sees; make it predictable.

**The API (the product).** `kebab-case`, `<domain>-<resource>-api`, no version in the name.
- Good: `product-developer-registration-api`, `finance-invoice-api`.
- Bad: `ProductAPI`, `apiV2`, `dev_reg_svc`.

**Base path and version.** Version lives in the URL as a major only: `/api/v{major}`. Minor/patch changes are backward compatible and do not change the path.
- `/api/v1/developers`

**Resources.** Plural nouns, `kebab-case`, hierarchical for ownership. Never verbs — the HTTP method is the verb.
- `GET /api/v1/developers`, `POST /api/v1/developers`, `GET /api/v1/developers/{developerId}`
- `GET /api/v1/developers/{developerId}/api-keys`
- Bad: `/getDeveloper`, `/api/v1/createDeveloper`.

**Path & query parameters.** `camelCase` path params (`{developerId}`); `camelCase` query params (`?pageSize=50&sortBy=createdAt`).

**JSON fields.** `camelCase`, no abbreviations that aren't universal, ISO 8601 for timestamps (`createdAt`), RFC 3339 UTC. IDs are opaque strings.

**HTTP methods & status codes (RFC 9110).**
| Action | Method | Success |
|---|---|---|
| List / read | `GET` | `200` |
| Create | `POST` | `201` + `Location` header |
| Full replace | `PUT` | `200`/`204` |
| Partial update | `PATCH` | `200` |
| Delete | `DELETE` | `204` |
Client error `4xx` (the caller can fix it); server error `5xx` (they can't). Never `200` with an error body.

---

## 2. Contract / YAML documentation (API First)

The contract is written and registered **before** implementation (`../schema/`). The contract is the single source of truth; code serves it exactly.

**Format:** OpenAPI **3.1** in `openapi.yaml`, kept in `domains/<domain>/src/api/` (or the reference module). OpenAPI 3.1 is a strict superset of JSON Schema 2020-12 — reuse the same schemas the registry validates.

**Every operation must carry:**
- `operationId` in `camelCase` (`registerDeveloper`) — code generators and tests key off it.
- `summary` (one line) and `description` (what/why, not how).
- `tags` grouping by resource.
- Typed `requestBody` and every `responses` entry, including at least one `4xx` and the `5xx`, each referencing a schema.
- At least one realistic `example` per request and response.

**Errors are uniform (RFC 7807 `application/problem+json`).** One error schema for the whole API:
```yaml
Problem:
  type: object
  properties:
    type:     { type: string, format: uri }   # stable doc URL for the error
    title:    { type: string }                 # short, human, does not change per-occurrence
    status:   { type: integer }
    detail:   { type: string }                 # this occurrence
    instance: { type: string, format: uri }
```

**Components over repetition.** Define schemas once under `components/schemas` and `$ref` them; the same schema object is what gets registered (Schema Registry First).

**A minimal worked example** — the smallest `openapi.yaml` that already satisfies every rule above (one operation, one success response, one error response, one `$ref`'d schema):
```yaml
openapi: 3.1.0
info:
  title: Product Developer Registration API
  version: 1.0.0
paths:
  /developers:
    post:
      operationId: registerDeveloper
      summary: Register a developer
      description: Registers a new developer; the email must be unique.
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/RegisterDeveloperRequest' }
            example: { email: ada@example.com, displayName: Ada Lovelace }
      responses:
        '201':
          description: Developer registered.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Developer' }
        '400':
          description: Validation failure.
          content:
            application/problem+json:
              schema: { $ref: '#/components/schemas/Problem' }
components:
  schemas:
    RegisterDeveloperRequest:
      type: object
      required: [email]
      properties:
        email:       { type: string, format: email }
        displayName: { type: string }
    Developer:
      type: object
      required: [id, email, status, createdAt]
      properties:
        id:        { type: string }
        email:     { type: string, format: email }
        status:    { type: string, enum: [active, suspended] }
        createdAt: { type: string, format: date-time }
    # Problem: the one shared error schema for the whole API — defined once, above.
```
The full, runnable version of this same contract — every endpoint, every schema, every error case — is `../../delivery-lab/reference-implementations/api/developer-registration-api/openapi.yaml`; read the snippet above as its smallest possible slice, not a separate example to reconcile against it.

**Documentation is generated, not hand-kept.** Human-readable API docs are rendered from `openapi.yaml` in CI (e.g. Redocly/Swagger UI) and published — never written twice. If the docs and the YAML disagree, the YAML wins.

**What every registered API contract must document, at minimum** — the checklist a reviewer (or the registry) actually checks:
- The artifact's registry coordinates (group/artifact, §"Apicurio" below) and current version.
- The compatibility rule declared for it (§3).
- `operationId`, `summary`/`description`, typed request/response schemas, at least one `4xx` + the `5xx`, and a realistic `example` — all already required above.
- A one-line "who owns this" — the domain/team a consumer contacts about a breaking change.

### Schema registry tooling: Apicurio

**What it is.** Apicurio Registry is an open-source content registry for API and schema artifacts — OpenAPI, AsyncAPI, JSON Schema, Avro, Protobuf, GraphQL. It is the concrete tool behind the `../schema/` capability's **Schema Registry First** principle: once `openapi.yaml` is registered, it exists as one versioned, addressable **artifact** that every consumer resolves — not a file two teams each keep their own copy of.

**How it works.**
- An artifact is registered under `group:artifact` coordinates — use the domain/API names from §1 (e.g. group `product`, artifact `developer-registration-api`), so the coordinates are guessable from the API's own name.
- Each registration is a new **version** of that artifact; Apicurio content-hashes it, so re-registering an unchanged file is a no-op rather than a new version.
- On every registration, Apicurio evaluates the artifact's declared **compatibility rule** (BACKWARD/FORWARD/FULL/NONE) against the previous version — this is the mechanism §3's schema-compatibility check runs on; an incompatible push is rejected by the registry itself, not caught only by a human reviewer.
- Producers, consumers, and codegen tools resolve the artifact by coordinates + version (or `latest`) through Apicurio's REST API or its Maven/Gradle/CLI clients. This is what makes "the contract test validates against the *registered* schema" (§3) literal rather than aspirational: the test pulls from Apicurio, it does not read a local file that could have silently drifted.

**Purpose and objective.** Apicurio exists to make Schema Registry First enforceable, not just documented policy: a contract registered once is discoverable by every other domain (reuse and governance), a breaking change is rejected before it reaches a consumer (safety), and a deprecated version stays resolvable indefinitely for audit and replay — the same indefinite-schema-retention rule `../event/guidelines.md` §4 states for event schemas. One registry, one mechanism, serving API, Event, and Data contracts alike.

**Versioning (SemVer 2.0.0 applied to the contract).**
- **Patch/minor** = backward compatible (add optional field, add endpoint) → same `/v1`.
- **Major** = breaking (remove/rename field, change type, tighten validation) → new `/v2`, and the old version enters deprecation with a `Sunset` header (RFC 8594) and a documented window.

---

## 3. Testing → automation → build

Follow the test pyramid; every layer is automated in CI and gates the build. Nothing merges red.

1. **Unit tests** — pure logic (validation, mapping, business rules), no network. Fast, run on every commit. Target the handler and domain functions directly.
2. **Contract tests** — the running API's responses are validated against the **registered** `openapi.yaml`. This is the load-bearing test in UIF: it proves code and contract have not drifted. A request/response that isn't in the contract fails the build.
3. **Integration tests** — the API against its real dependencies (DB, queue) using ephemeral containers (e.g. Testcontainers) or a spun-up compose stack in CI.
4. **Consumer-driven contract tests** — where the API has known consumers, each consumer's expectations are verified (e.g. Pact) so a provider change can't silently break a consumer.
5. **End-to-end / smoke** — a thin happy-path check against a deployed environment after CD, not in the unit gate.

**Automation & build gate (GitHub Actions, at repo root — see `../delivery/`).** On every PR touching `domains/<domain>/**`:
```
lint  →  unit  →  contract (vs registered schema)  →  build container (src/Dockerfile)  →  security scan  →  publish rendered API docs
```
- Coverage threshold enforced (start at a realistic floor, ratchet up — don't fake 100%).
- The build artifact is the **one container image** from `src/Dockerfile`; the image, not the source, is what CD promotes.
- Contract validation and the schema-registry compatibility check both run here; a breaking schema change fails the PR.

**Test layout** lives with the domain in `qe/` (strategy + suites) and alongside code in the reference module's `tests/`.

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Security by default (`../security/`).** No unauthenticated endpoints. OAuth 2.1 / OIDC bearer tokens; authorise every operation (least privilege); validate all input against the schema; never log secrets or PII. Declare the security scheme in `openapi.yaml` (`components/securitySchemes`) so it's part of the contract.
- **Observability by default (`../observability/`).** Emit the RED signals (Rate, Errors, Duration) per endpoint; structured JSON logs with a correlation/trace id; propagate W3C `traceparent`. Define an SLO (e.g. p99 latency, availability) before shipping.
- **Idempotency.** `GET/PUT/DELETE` are idempotent by definition; make `POST` idempotent for retries with an `Idempotency-Key` header where a duplicate would do harm.
- **Pagination, filtering, sorting** are cursor- or page-based and documented in the contract; never return an unbounded list.
- **Backward compatibility is a promise.** Additive within a major; breaking only across a major with a published deprecation + `Sunset`.

---

## 5. Definition of done (API)

- [ ] Name follows; contract in `openapi.yaml` follows §2 and is **registered before code**.
- [ ] Unit + contract tests pass; contract test validates against the registered schema (3).
- [ ] Security scheme + authz in the contract and enforced (§4); no unauthenticated endpoint.
- [ ] Observability signals + one SLO defined (4).
- [ ] CI green: lint → unit → contract → container build → scan → docs published (3).
- [ ] Rendered API docs published from the YAML (not hand-written).
- [ ] Maps to a Jira Story (`capability:api` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

**This standard is language-agnostic.** A domain builds to one container image (`../delivery/`) in whatever stack it chooses; the naming, contract, tests, security, and observability rules above are identical. The same contract is implemented in two runnable references to make that concrete:
- **TypeScript / Express:** `../../delivery-lab/reference-implementations/api/developer-registration-api/`
- **Python / FastAPI:** `../../delivery-lab/reference-implementations/api/developer-registration-api-fastapi/`

Both serve the identical `openapi.yaml`, return RFC 7807 errors, enforce bearer auth, and contract-test their responses against the registered schema (Ajv 2020 in TS, `jsonschema` Draft 2020-12 in Python).

Golden-path narrative: `../../delivery-lab/blueprints/api-playbook.md`.
