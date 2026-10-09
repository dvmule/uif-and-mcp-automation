# API Playbook

The golden path for building a governed API in UIF, from a Jira Story to a running, operated API. This is the worked example other playbooks follow. It assumes you have read `../../architecture-design/operating-model/fde-build-deploy-workflow.md` and the API capability standards (`../../capabilities/api/`), especially the worked standard `../../capabilities/api/guidelines.md`.

**Capability:** API · **Standard:** `../../capabilities/api/guidelines.md` · **Runnable reference:** `../../delivery-lab/reference-implementations/api/developer-registration-api/`

An API ships **inside a domain**, not as a standalone module: the code lives in `domains/<domain>/src/api/`, the contract in `domains/<domain>/src/schemas/`, and the whole domain builds to one container image from `domains/<domain>/src/Dockerfile`. See `../../capabilities/docs/capability-domain-jira-map.md` for how this one slice maps across GitHub and Jira.

## Prerequisites

- A **Story** parented to the domain's **Domain epic**, labelled `capability:api` (+ any other `capability:*` it touches) per `../../capabilities/docs/capability-domain-jira-map.md`.
- Read the four principles that gate the work: API First, Schema Registry First, Secure By Default, Observable By Default (`../../architecture-design/principles/`).

## Steps

1. **Name it** (`guidelines.md` §1). API `<domain>-<resource>-api`, base path `/api/v{major}`, plural-noun resources, `camelCase` fields, RFC 9110 status codes.
2. **Write the contract** (`guidelines.md` §2). Author `openapi.yaml` (OpenAPI 3.1) in `domains/<domain>/src/schemas/` — before code (API First). Every operation carries `operationId`, `summary`, typed request/response, a `4xx`, the `5xx`, examples, and the shared RFC 7807 `Problem` error schema. Declare the security scheme in the contract.
3. **Register the schema.** Register the contract in the Schema Registry and confirm compatibility (`../../stacks/apicurio/standards/`) — before implementing.
4. **Design security in** (`guidelines.md` §4, `../../capabilities/security/`). OAuth 2.1 / OIDC, authorise every operation, validate all input, no unauthenticated endpoint.
5. **Design observability in** (`guidelines.md` §4, `../../capabilities/observability/`). RED signals per endpoint, structured JSON logs with a correlation id, W3C `traceparent`, one SLO.
6. **Implement** the handlers in `domains/<domain>/src/api/`, serving the registered contract exactly.
7. **Test** (`guidelines.md` §3). Unit + contract tests; the contract test validates the running API against the registered `openapi.yaml`. Strategy and suites live in `domains/<domain>/qe/`.
8. **CI (GitHub Actions).** The repo-root workflow (`../../.github/workflows/`) runs `lint → unit → contract → build container (src/Dockerfile) → security scan → publish rendered docs` on any PR touching `domains/<domain>/**`. Merge only on green (`../../capabilities/delivery/`).
9. **CD (GitHub Actions).** The reusable root `.github/workflows/domain-cd.yml` promotes the one image Development → UAT → Production.
10. **Run** on AWS/Kubernetes via `domains/<domain>/infrastructure/{terraform,kubernetes,helm}`.
11. **Operate in JSM** (`../../jira/jsm-alignment.md`).

## Done when

The work meets the API Definition of Done in `../../capabilities/api/guidelines.md` §5: named per §1; contract registered before code; unit + contract tests green; security + authz enforced; observability signals + one SLO defined; CI green through container build + scan + docs; rendered docs published from the YAML; and the Story is parented to its Domain epic with the right `capability:*` label.
