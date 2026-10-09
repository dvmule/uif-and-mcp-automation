# API

## Purpose
Synchronous, request/response capabilities exposed and consumed as governed, discoverable products rather than point-to-point connections. This capability defines how an API is designed, registered, secured, and shipped in UIF.

## Standards
- **API First:** the contract (`openapi.yaml`) is designed and registered before implementation, into the Apicurio schema registry (`../schema/`).
- Every API is authenticated and authorised by default (`../security/`) and emits standard telemetry (`../observability/`).
- Versioning and backward compatibility are explicit; breaking changes require a new version.

## Governance
- **Central:** API design standards, the contract format, auth model, the registry.
- **Local (FDE):** the API's resource model and business logic.

## Patterns
- Contract-first design; registry-validated in CI.
- Consumer-driven contracts where an API has known consumers.

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — how an FDE names an API, documents its contract in YAML, tests it from unit through automation, and builds it, on globally recognised standards (OpenAPI 3.1, JSON Schema 2020-12, RFC 9110, RFC 7807, SemVer 2.0.0, OAuth 2.1/OIDC). This is also the template shape every other capability's `guidelines.md` follows.
- **Runnable references (same contract, two stacks — the standard is language-agnostic):** `../../delivery-lab/reference-implementations/api/developer-registration-api/` (TypeScript/Express) and `../../delivery-lab/reference-implementations/api/developer-registration-api-fastapi/` (Python/FastAPI) — each implements every rule in `guidelines.md` end to end.
- **Golden path:** `../../delivery-lab/blueprints/api-playbook.md`. **Template:** `../../delivery-lab/templates/`.
