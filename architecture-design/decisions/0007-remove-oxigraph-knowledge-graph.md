# ADR-0007 — Remove Oxigraph and the RDF Knowledge Graph from the Schema Registry

**Status:** Accepted
**Date:** 2026-08-10
**Decider:** Rajnivas Karunakaran (repository owner)
**Scope:** The Pearson Schema Registry service under `capabilities/schema/`. Removes the Oxigraph triple store and everything built on it — across `api/`, `webapp/`, and `infra/`.
**Supersedes in effect:** the RDF-twin substrate established by W1-S5 and consumed by W1-S6 (D3 drift), W1-S8, W2-S8, W2-S9, and W2-S10. Those stories are undone, not merely paused.
**Relates to:** [ADR-0006](0006-schema-registry-under-capabilities.md) (why this service sat under `capabilities/`; superseded by [ADR-0012](0012-schema-registry-moves-to-stacks-apicurio.md)).

> **Scope amendment, same day (2026-08-10).** After the removal below was applied, the owner confirmed the intent is broader than dropping a datastore: **this service is to be purely a schema registry.** The Knowledge Graph *and* the Business Assurance Agent integration are removed outright — not deferred, not pending a replacement. Accordingly the residual BAA surface (the stub "BAA Scan" button), the "Enterprise Digital Twin" framing in `pyproject.toml` and `app/__init__.py`, and the Knowledge-Graph copy on the Simulate Deploy page were all removed too, and consequence 1 below is restated as a decision rather than an open problem. **`capabilities/knowledge-graph/` and `capabilities/agent/` themselves are NOT removed** — see "Left deliberately in place".

---

## Context

The registry mirrored all Apicurio state into an Oxigraph RDF triple store — "the digital twin" — and exposed it by SPARQL to the UI and to the Business Assurance Agent. Six named graphs were in use (`g:live`, `g:drift`, `g:cmdb`, `g:attributes`, `g:mappings`, `g:semantic`), fed by two polling extractors, with locked canonical IRI bases under `https://schema.pearson.com/`.

The owner directed its removal. This was **not** a marginal call and the decision was taken with the cost stated in advance: the twin was load-bearing across 18 `api/app` modules, 10 `webapp/src` files, 15 test files, and one of seven Terraform modules — and every Wave 2/Wave 3 story was specified against it. The removal was chosen anyway, over two narrower alternatives that were offered and declined:

- **Drop only the `ecs-oxigraph` Terraform module**, keeping all application code and supplying `OXIGRAPH_BASE_URL` as a plain variable.
- **Drop the module and repoint the BFF at a different SPARQL store** (Neptune, managed, or a shared platform KG), keeping the twin intact.

Recording this because the removal reverses accepted design decisions rather than completing them, and a future reader deserves to know it was deliberate rather than drift.

## Decision

**Oxigraph and every feature derived from it are removed.**

### `api/` — deleted

`clients/oxigraph_client.py` · `extractors/` in full (`live_extractor.py`, `mapping_extractor.py`, `rdf_builder.py`) · `services/{graph_service,drift_service,attribute_graph_service,cmdb_sync}.py` · `services/drift_detectors/` · `api/v1/{twin,drift}.py` · `schemas/{graph,attribute_graph,drift}.py` · 10 test modules.

### `api/` — amended

| File | Change |
|---|---|
| `main.py` | Both extractor background tasks and the CMDB graph sync unwired. The lifespan now holds only the Apicurio client; **there are no background tasks.** |
| `api/v1/__init__.py` | `twin` and `drift` routers dropped |
| `api/v1/health.py` | Apicurio is the only dependency checked; `degraded` has exactly one cause |
| `core/config.py` | `oxigraph_base_url`, `twin_sparql_token`, `extractor_poll_interval_seconds` removed |
| `core/dependencies.py` | `get_oxigraph_client` removed; `OnboardingService` no longer takes it |
| `core/exceptions.py` | `OxigraphError → HTTP 503` handler removed |
| `services/onboarding_service.py` | No longer writes producer/consumer edges to `g:live`. **The CMDB manifest update is now the only record of the produces/consumes relationship.** |
| `schemas/service.py` | `OnboardResult.graph_updated` removed — a response-shape change for any client reading it |

`TopicReadiness.drift` **stays.** Its `"D6"` value comes from an Apicurio 404 plus the topic's role, never from the graph — it is unrelated to D3 drift detection.

### `webapp/` — deleted

`features/twin/` · `features/drift/` · `pages/TwinExplorerPage.tsx` · `pages/DriftInventoryPage.tsx` (+ test).

### `webapp/` — amended

The `drifting` tab is gone from the governance dashboard, which required unpicking it from `use-schema-filter.ts` (both `filterSchemas` and `computeTabCounts` lose their `driftingIds` parameter), `use-schema-url-state.ts` (the `ViewMode` union), `SchemaTabs.tsx`, `SchemaTable.tsx` (the `drift` badge), `TopicsTable.tsx` (V/K badge tone now keys off compliance only), `RegistryHealthRail.tsx` (the Drifting card and the "New drift detected" counter), `SchemaListPage.tsx`, `App.tsx` (the `/twin` route and the `/drift` redirect), and `AppShell.tsx` (the Twin nav link).

No dependency became dead: `@xyflow/react` and `@dagrejs/dagre` are still used by the services feature's own dependency graph, which is CMDB-derived.

### `infra/` — deleted and renamed

- `terraform/ecs-oxigraph/` deleted (11 files).
- `terraform/ecs-apicurio/` → **`terraform/apicurio-rds/`**
- `terraform/ecs-bff/` → **`terraform/apicurio-bff/`**

Renaming inverts the pre-existing `<platform>-<service>` convention (`s3-frontend`, `apigw-bff`, `ecs-cdc-poller` still follow it). Accepted as the owner's naming choice; the estate is now mixed.

`apicurio-bff` lost three Oxigraph couplings: the `terraform_remote_state.oxigraph` data source, the `OXIGRAPH_BASE_URL` container env var, and the tcp/7878 egress rule to Oxigraph's ALB. Every cross-module reference to the two old directory names was repaired (`apigw-bff/main.tf`'s remote-state path, plus comments and `*.tfvars.example` files).

## Consequences

### Verified at the time of the change

- `pytest` — **104 passed, 6 skipped** (was 236 passed / 10 skipped; the 4 skipped SPARQL integration checks are gone too).
- `vitest` — **134 passed** (was 165), `tsc -b` clean, `vite build` produces the S3 bundle.
- `terraform fmt -check -recursive` clean; `terraform validate` passes on all 5 live modules under the new names (`ecs-cdc-poller` still skipped, per ADR-0006 consequence 5).

### What is lost

1. **The Business Assurance Agent integration — dropped by decision, not broken by accident.** The BAA consumed this service by SPARQL; `/api/v1/twin/sparql`, `/twin/query`, `/twin/graph`, `/twin/export`, and `/twin/attribute-graph` all return 404 now. The owner confirmed (2026-08-10, see the scope amendment above) that **the BAA is out of scope for this service entirely** — no replacement contract is owed. The stub "BAA Scan" button was removed from the governance dashboard for the same reason. If a BAA ever consumes this registry again it is new work under a new ADR, not a restoration.
2. **Drift detection.** D3 (orphan subjects) is gone, and with it the Wave 3 D1–D7 programme as specified — every detector was designed as SPARQL over `g:live`/`g:cmdb`.
3. **Field-level lineage.** The `g:attributes`/`g:mappings`/`g:semantic`/`g:lineage` graphs and the 3-column attribute-lineage view (W2-S10) are gone.
4. **Cross-restart state.** `validation_cache.py` was to migrate into `g:drift`. It stays in-process, so validation state is lost on restart and invisible outside the process; a durable store is needed if that matters.
5. **The locked canonical IRIs** (`https://schema.pearson.com/ontology/v1#`, `/graphs/`, `/resource/`) are no longer minted by anything. They were documented as permanent, on the grounds that changing them breaks all SPARQL queries — that constraint is now moot here, but any *other* consumer of those IRIs across the estate should be checked before they are considered free.

### Left deliberately in place

- **`capabilities/knowledge-graph/` and `capabilities/agent/` — deliberately NOT removed**, despite this service no longer realizing either. They are two of UIF's **thirteen capabilities**, which `CLAUDE.md` states are settled; removing one is a change to the Engineering Operating System, not a cleanup of this service. Each is load-bearing well beyond `capabilities/schema/`:
  - named in the thirteen in `CLAUDE.md`, `README.md`, `jira/jira-workflow.md`, `jira/confluence-pointer-draft.md`, `delivery-lab/starter-kits/README.md`, `_domain-template/README.md`, and `.claude/skills/pm/SKILL.md`;
  - a Jira Capability with a `Knowledge Graph Foundation` epic and a store-selection Spike (`jira/backlog-seed.md`, `jira/capability-model.md` §6, `PLANNING.md`);
  - an engineering principle — *Knowledge Graph Enabled* (`architecture-design/principles/README.md`);
  - an opt-in scaffold target in `create-domain.sh` (`--caps knowledge-graph`);
  - **already consumed by another domain** — `domains/assessment/src/knowledge-graph/` exists.

  The correct reading is that the KG capability's Governance claim ("Central: the graph store, the ingestion pattern, the query interface") is now **unrealized**, not wrong. Whether UIF wants a central graph store at all is a capability-level question for the framework owners, and needs its own ADR.
- **A trail of comments** at each amended site (`main.py`, `health.py`, `config.py`, `validation_cache.py`, `onboarding_service.py`, the Terraform files, `App.tsx`, `AppShell.tsx`) pointing here, so the absence reads as a decision rather than an oversight.

### Not done

- The `pearson-schema-registry-v2` source repo is untouched and still contains the full twin, including `docs/sparql-queries/` and its own ADR-0002 (Oxigraph dev server). If that repo is retired as planned, the twin's only remaining copy is its git history.
- `docker-compose.yml` in the source repo still starts an `oxigraph` service. It was never migrated (ADR-0006), so nothing here contradicts it.
