# program — domain skill

Context for engineers and AI assistants working in the `program` domain.

## What this domain owns

Silver → Gold entity materialization for the **Program** entity:
`gold_program.program`. A program is a PDH item whose `SEGMENT1` starts `P2`,
read at `ORGANIZATION_ID = 82`; the gold table carries one row per
`(inventoryItemId, organizationId)`.

The eighth domain, added 2026-08-11 by framework-owner decision. It resolves
[ADR-0005](../../architecture-design/decisions/0005-gold-layer-shape.md)'s open
Program question ("a separate entity is justified only if programs carry
attributes of their own") — programs carry twelve of their own, ten scalar and
two arrays. `associatedPrograms` stays on `gold_product.product` as the reference
from product → program; this domain owns everything about the program itself.

**Boundary it does not cross:** the product entity. Program reads the same
Silver tables as product but materializes a different entity at a different
grain. Nothing here writes `gold_product.*`, and product's mapping is not
edited from this domain.

Architecture: [ADR-0001](../../architecture-design/decisions/0001-gold-layer.md)
(gold foundations), [ADR-0003](../../architecture-design/decisions/0003-gold-incremental-cdc.md)
(incremental CDC — **read before touching the pipeline**),
[ADR-0004](../../architecture-design/decisions/0004-independent-gold-control-plane.md)
(control plane, cutover), [ADR-0005](../../architecture-design/decisions/0005-gold-layer-shape.md)
(gold layer shape; the Program decision). Delivery epic:
[UIFPE-116](https://pearson.atlassian.net/browse/UIFPE-116).

## Component map — what exists and what does not

| | State |
|---|---|
| `src/schemas/gold/program/v1/master.json` | **DONE** — the PRIMARY artifact: 12 properties, types, `x-channels`, array shapes. Declares its own key in `metadata.primaryKey` |
| `src/schemas/mapping/program/v1/program.mapping.json` | **DONE** — mapping rules only: `drivingTable`, `entityKey`, the `programImageUrl` transform, 12 columns |
| `src/schemas/mapping/program/v1/manifest.yaml` + `sql/program.sql` | **MISSING** — [UIFPE-120](https://pearson.atlassian.net/browse/UIFPE-120). Nothing can run without them |
| pipeline config (`src/apps/pipelines/`) | **MISSING** — [UIFPE-121](https://pearson.atlassian.net/browse/UIFPE-121) |
| CI + deploy workflow | **MISSING** — [UIFPE-121](https://pearson.atlassian.net/browse/UIFPE-121). `domain-ci.yml` already covers this domain (it globs all domains); the schema-validation and deploy workflows are still product-scoped |
| Terraform | **NONE, by design so far** — program reuses the shared gold stack; see "Boundaries" |

## Boundaries (what lives where)

- **This domain:** the program entity's schema, mapping rules, manifest, SQL,
  pipeline config and (once it exists) its deploy workflow.
- **Reused, not owned — `stacks/echo/src/apps/`:** the gold runtime
  (`pipeline/generic_gold_transform.py`, `gold_cdc.py`) and the control-plane
  CLIs (`cli/sync_pipelines.py`, `generate_tier_subsets.py`,
  `build_mapping_artifact.py`, `compile_intake_delta.py`, `publish.sh`). They
  are entity-agnostic — driven by `gold_id` config — and are Echo's since
  UIFPE-358 phase 2
  ([ADR-0018](../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md)).
  This domain's workflow calls them by path, as
  [`capabilities/data/gold-control-plane.md`](../../capabilities/data/gold-control-plane.md)
  records.
- **Platform repo (`uif-relay-echo`) — READ-ONLY, another team:** Bronze/Silver
  ingestion and the shared primitives program consumes by reference —
  `uif-echo-{env}-gold-artifacts` and the Glue jobs' EventBridge rules.
  The execution role, scripts bucket and metrics table are NOT the platform's
  any more: the gold jobs moved onto the standard estate's own
  `uif-echo-{env}-glue-role`, `uif-echo-{env}-data` and
  `uif-echo-{env}-pipeline-metrics`, all managed in this repo.

## The entity

Grain: one row per `(inventoryItemId, organizationId)`. Entity key declared in
BOTH artifacts — `metadata.primaryKey` on master.json (this is the first entity
to do that) and `entityKey` in the mapping.

| | |
|---|---|
| Row filter | `SEGMENT1 LIKE 'P2%' AND ORGANIZATION_ID = 82` — belongs in the manifest's `sot_filter`; it currently rides on two key columns' `source.filter` because the intake contract has no entity-level filter field |
| Sources | `MTL_SYSTEM_ITEMS_B` (driving) · `MTL_SYSTEM_ITEMS_TL` · `EGO_MTL_SY_ITEMS_EXT_B` (groups 1421/1422/1423/1463) · `MTL_RELATED_ITEMS` · `FND_LOOKUP_VALUES_VL` — all in `uif_echo_dev_silver_mdm`, all carrying `_silver_updated_at` |
| Arrays | `associatedProducts` (value array) · `relatedPrograms` (struct array) |
| Channels | all 10 non-key columns on event+message+api; the 2 key columns `x-channels: []` (stored, carried by no feed) — same as product |

## Key invariants and gotchas — read before changing anything

- **`FND_LOOKUP_VALUES_VL` has no honest key_mapping.**
  `validate_key_mappings` refuses any source whose key_mapping keys are not a
  subset of the gold PK, and the lookup table has neither key column. It is
  declared with the **constant fan-out** `organizationId: "CAST(82 AS BIGINT)"`
  — the whole org half of the PK, so a relationship-type rename invalidates
  every program row, which is correct. This mirrors product's `org_params`
  treatment; **do not narrow it to dodge the cost** (product's manifest says
  the same, for the same reason). The canonical hash gate absorbs spurious
  fan-out: unchanged rebuilt rows write nothing.
- **`associatedProducts` cannot be change-detected.** Its link rows live in
  EAV group 1421, keyed by the **product's** item id, naming the program only
  as a PPID *string* — and `build_key_detection_sql` is a single-table
  projection, so PPID → inventoryItemId (which needs a join) is inexpressible.
  Mitigation: a **nightly targeted refresh after the incremental** —
  `--refresh_columns associatedProducts --refresh_mode emit`. Deliberately NOT
  `--rebuild`: rebuild writes no change-log rows, so it would repair the table
  while telling no consumer. The proper fix is a program-keyed Silver view of
  group 1421; raise it with the Silver team.
  **`relatedPrograms` is NOT affected** — `MTL_RELATED_ITEMS` is keyed by the
  program's own PK.
- **Empty arrays are `[]`, never NULL** (`COALESCE(x, array())`), matching
  product. This is a permanent hash-stability commitment.
- **Two-hop arrays need extra aliases.** Phase 2 replaces the driving alias's
  temp view, so any CTE that must read the FULL driving table — resolving an
  associated product's or related program's `SEGMENT1` — needs a second/third
  alias over `mtl_system_items_b` declared in **both** the manifest and the
  pipeline config.
- **Gold column naming is camelCase**; framework metadata columns keep their
  `_snake_case` prefix. Athena lowercases unquoted identifiers — cosmetic only.
- **`src/Dockerfile` builds nothing.** This is a Glue/Lambda workload; the file
  exists solely because `domain-ci.yml` makes it an unconditional gate. Product
  carries the same unresolved tension — see its CLAUDE.md; an ADR is owed.
- **`ui/` and `ux/` are empty stubs** the mould forces (`TOP_DIRS` has no
  opt-out). Program's mapping is authored through product's Transform UI. This
  sits against the repo's "don't create empty stub files" rule; recorded, not
  hidden.

## Common workflows

Run from the monorepo root. `py` is the Windows launcher; `python3` elsewhere.
The control-plane CLIs live in `stacks/echo/src/apps/cli/` (ADR-0018).

| Task | Command |
|------|---------|
| **Test** | `py -m pytest domains/program/qe -v` |
| **Validate the intake against the schema** | `py -m pytest domains/program/qe/test_intake_hygiene.py -q` |
| **Sync pipeline configs (plan)** | `py stacks/echo/src/apps/cli/sync_pipelines.py --env dev --repo-root domains/program/src/apps` |
| **Trigger the gold transform** | `aws lambda invoke --region eu-west-1 --function-name uif-echo-dev-gold-dispatch --payload $(echo -n '{"frequency":"gold-tick"}' \| base64) /tmp/r.json` — program is on the daily rung (UIFPE-121); the payload must match its `schedule.frequency` |
| **Manual array repair** | `aws glue start-job-run --region eu-west-1 --job-name uif-echo-dev-gold-transform --arguments '{"--gold_id":"program","--refresh_columns":"associatedProducts","--refresh_mode":"emit"}'` — refuses to run while any source has pending deltas, so run it AFTER the incremental |

## Provisional placements & open decisions

1. **No Terraform of its own.** Program expects to reuse the shared gold stack
   under `domains/product/infrastructure/terraform/` (Glue jobs, dispatcher,
   IAM). Whether that stack moves somewhere shared now that a second domain
   depends on it is an open governance question — repo rule 3 puts infra under
   `domains/<domain>/`.
2. **`uif_echo_{env}_gold_program` creation** — runtime-created by the transform
   on its first run (settled at UIFPE-358 phase 1); the config sync tags it.
3. **Shared tooling home** — [UIFPE-122](https://pearson.atlassian.net/browse/UIFPE-122).
4. **Registry publishing deferred** (`lifecycle: draft` in master.json). Blocked
   on a service URN, `PUBLISH_API_BASE`, and a `publish-schema` skill that does
   not exist yet.
5. **Platform confirmations outstanding:** does `uif-echo-{env}-glue-role` permit
   SELECT on `mtl_related_items` + `fnd_lookup_values_vl` and create/write on
   `uif_echo_{env}_gold_program` and `s3://uif-echo-{env}-gold/program/*`? Is the
   catalog Lake-Formation-governed?
6. **`coverImageLargeUrl` — mostly settled (2026-08-11).** The `/P` DAM prefix and
   the DAM asset space are confirmed by the mapping owner (program shares
   product's asset space, so the inherited host + GUID are correct). Still
   unconfirmed: whether the inherited `/coverimage%` / `/bigcovers%`
   site-relative branches occur in ATTR_GROUP_ID 1423. Kept deliberately — an
   unmatched branch is inert, dropping them could lose real values. Confirm on
   the first load.
7. **`SEGMENT1 LIKE 'P2%'` completeness** — product excludes ALL `P%` items, so
   `P1%`/`P3%` items would currently belong to no gold table. Intended?

## Ownership (Four-in-a-Box)
FDE: `src/`, `infrastructure/` · QE Lead: `qe/` · UX Lead: `ui/`, `ux/` · PM: scope & Jira trace.
Covered by the CODEOWNERS wildcards — `@uif-fde` by default, `@uif-qe` for `qe/`, `@uif-ux` for `ui/`+`ux/`.
