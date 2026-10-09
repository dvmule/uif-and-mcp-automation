# apicurio / qe

Tests and quality for the governance layer this stack runs in front of the
Apicurio Registry — the BFF (`../src/`) and the dashboard (`../ui/`).

This folder used to say it was deliberately empty, on the grounds that the
stack deployed nothing but an off-the-shelf image. That stopped being true
when the BFF and UI moved here from `capabilities/schema/`
([ADR-0012](../../../architecture-design/decisions/0012-schema-registry-moves-to-stacks-apicurio.md)).
The registry container itself is still off-the-shelf and still has no tests
of ours — everything here covers the layer in front of it.

## Layout

| Path | Covers |
|---|---|
| `tests/` | The BFF: routers, services, clients. `pytest`, async via `pytest-asyncio`. |
| `tests/integration/` | Runs against a live local Apicurio. Skipped unless `RUN_INTEGRATION_TESTS=1`. |
| `../ui/src/**/*.test.tsx` | The dashboard. `vitest` + React Testing Library, colocated with components by Vite convention rather than living here. |
| `../ui/e2e/` | Playwright end-to-end. |

`conftest.py` is the **only** place that edits `sys.path` for this suite —
the same convention `stacks/beacon/qe/conftest.py` and
`domains/product/qe/conftest.py` follow. It puts `../src` on the path so
`from app...` resolves exactly as it does inside the image.

## Running

```bash
cd stacks/apicurio
pytest                      # 119 passed, 12 skipped

# Against a real registry. The 12 skips above are these plus the BFF
# connectivity tests, which need BFF_URL set to a deployed BFF.
docker compose up -d
RUN_INTEGRATION_TESTS=1 pytest qe/tests/integration/test_registry_v3.py   # 6 passed
docker compose down -v

cd ui
npm ci && npx vitest run    # 134 passed
npm run build               # tsc -b && vite build
```

`pytest.ini` at the stack root is what makes the bare `pytest` above work:
it sets `asyncio_mode = auto` and `testpaths = qe/tests`. It has to restate
`--import-mode=importlib` from the repo-root `pytest.ini` because pytest
reads exactly one inifile — the nearest one — so anything omitted there is
simply unset for this suite.

## What is mocked, and what that costs

- **Apicurio** is mocked with `respx` in the unit tests, so on their own they
  verify the v3 port against the upstream OpenAPI document for tag `v3.3.2`
  rather than against a server — client and fixtures built from one document
  agree with each other whether or not the document is right.
  `tests/integration/test_registry_v3.py` exists to break that circle: it runs
  against a real 3.3.2 registry (`docker compose up -d` at the stack root) and
  confirms the two shapes ADR-0012 could only assert from the spec —
  `branch=latest` resolution and the `CreateArtifact` request body. It also
  pins what the spec got wrong: readiness is on the management port 9000 at
  `/health/ready`, and `/q/health/ready` 404s (UIFPE-334). These tests skip
  rather than fail when no registry is reachable, so they are safe to leave in
  a default `pytest` run.
- **The BFF** is mocked with MSW in the UI tests. No component test does real
  network I/O.
- **The CMDB** is not mocked in tests — it *is* a mock in production. There is
  no ServiceNow client, so every ownership assertion in this suite exercises
  fixture data, and passing tests say nothing about real ownership resolution.
