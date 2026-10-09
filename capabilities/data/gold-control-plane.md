# Data — Gold control-plane tooling: stewardship and reuse (the worked standard)

Who owns the Silver→Gold control-plane tooling, where it lives, and how a gold
domain other than `product` uses it. This is the recorded decision of
**UIFPE-122**: the tooling is **platform-scope code hosted in
`domains/product/`** — a steward model — consumed by other gold domains at the
sanctioned paths below, with extraction to a shared home deferred to an
explicit trigger. The Data capability's other documents govern data as an
owned product; this one governs the machinery that builds gold tables at all.

## 1. The decision: steward model — hosted in product, consumed by reference

The gold control-plane tooling (`compile_intake_delta.py`,
`generate_tier_subsets.py`, `build_mapping_artifact.py`, `sync_pipelines.py`,
`gold_config.py`, `gold_frequencies.py`, `validate_gold_schemas.py`,
`publish.sh`) and the runtime (`generic_gold_transform.py`, `gold_cdc.py`, the
dispatcher Lambda, the pipelines config table) are **entity-agnostic by
design** — driven by `gold_id` config and per-entity artifact paths — but they
physically live in `domains/product/`, the domain that built them.

**Both obvious alternatives are rejected:**

- **Copy-per-domain** is a drift factory. The compiler alone carries a
  ~1,000-line test suite, a census gate, and a Python↔TypeScript parity
  fixture; `sync_pipelines.py` writes a config table that is ONE table for
  every gold entity — two copies means two writers with diverging rules
  against shared state.
- **Extract-to-shared now** is blocked twice over: repo rule 1 places all
  implementation code under `domains/<domain>/`, and the one sanctioned
  escape — a top-level `platform/` home — is ADR-0006 D5, still an open
  decision on an unmerged branch. Extracting on the strength of two consumers
  also violates the repository's own third-repetition rule.

So: **product hosts; others reference.** The reach-in that program's deploy
workflow marked "accepted for now" is, by this standard, *sanctioned* — at the
paths in §3 and nowhere else.

**Extraction trigger** (whichever comes first): a **third** gold domain goes
live (price and stock are next, both currently draft with placeholder source
tables), or **ADR-0006 lands** with a ruling on the `platform/` home. When it
fires, extraction is its own story — including which domain (or shared suite)
owns the ~1,000 tests that move with the compiler.

## 2. Runtime: one shared copy, confirmed

All gold entities run through the **same two entity-agnostic Glue jobs** at
`s3://uif-echo-{env}-data/code/gold/`, the same dispatcher, and the
same pipelines config table — the shared control plane ADR-0004 designed.
Program's first load, refresh, and A1–A8 acceptance (2026-08-20) all ran on
the shared jobs with zero program-owned runtime. This standard confirms that
as the rule:

- A gold domain other than product deploys **no Glue scripts** (a second
  uploader would mean two domains writing the same S3 objects), **no gold
  Terraform** (a second apply would race one state lock), and **no UI** (the
  Transform UI is the shared control panel; it deploys with product).
- Its deploy ships exactly: schema artifacts + mapping artifacts under its
  OWN entity prefixes, then a config sync.
- Deploy concurrency: per-domain groups are safe **only while the above
  holds** — the moment a second domain deploys the dispatcher or the shared
  jobs, the workflows must adopt one shared gate (`group: deploy-gold-dev`)
  or they will race. This condition, first recorded on UIFPE-121, is now
  standard.

## 3. Sanctioned paths (what another gold domain's workflows may invoke)

| Tool | Invocation from another gold domain | Caveat |
|---|---|---|
| `stacks/echo/src/apps/cli/validate_gold_schemas.py` | `--domain <domain>` | none — lifecycle-aware since UIFPE-121 |
| `stacks/echo/src/apps/cli/sync_pipelines.py` | `--env <env> --repo-root domains/<domain>/src/apps --apply` | the `--repo-root` flag is the multi-domain contract; never point two syncs at one repo-root |
| `stacks/echo/src/apps/cli/generate_tier_subsets.py` | `--master domains/<domain>/src/schemas/gold/<entity>/v1/master.json ...` | none |
| `stacks/echo/src/apps/cli/build_mapping_artifact.py` | `--manifest domains/<domain>/src/schemas/mapping/<entity>/v1/manifest.yaml ...` | none |
| `stacks/echo/src/apps/cli/compile_intake_delta.py` | `--intake <envelope> --repo-root .` | **structured artifacts and PLAN rendering are entity-agnostic; the AUTO SQL writers are product-shaped** (UIFPE-106): the CTE-*creation* paths anchor on product.sql's hand CTEs (`ag802`/`ag822` sections, the `orgs` dimension) and fail loudly — never silently — on an entity lacking them; the *extend*/driving/lookup paths work wherever the target SQL follows the same conventions (program's `agNNN` CTEs do). Until extraction parameterizes the anchors, review AUTO output on a non-product entity — the census gate's guarantees are pinned for product only |
| `stacks/echo/src/apps/pipeline/generic_gold_*.py`, `gold_cdc.py` | never invoked from another domain's workflow — reached only through the shared S3 copy (§2) | product's deploy is the only uploader |

Everything else in `domains/product/` — its Terraform, its UI build, its qe
suite, `publish.sh`'s registry publishing — is product's own; another domain's
workflow does not touch it. A reach-in outside this table is a boundary
violation, exactly as it was before this standard.

## 4. The full-alias pattern is general, not a program quirk

The transform runtime registers each manifest source as a temp view, and the
incremental (Phase 2) **replaces the driving alias's view with the
entity-restricted set** — only the rows being rebuilt. Any lookup that must
resolve OTHER entities' rows through the driving table therefore needs a
**second, FULL-registered alias** over the same table, declared in **both**
the manifest and the pipeline config.

Evidence this is a pattern, not a one-off (two domains, three uses):

- program `items_all` — `assoc` and `rel` resolve another item's `SEGMENT1`;
  `parent_prod` walks other items entirely (program.sql's own header calls
  reading `items` there "silently see only the rows already being rebuilt").
- product `items_all` — the `isbn_owner` index resolves parent ISBNs to
  OWNING products (product.sql, 2026-08-17 batch).

Rule: declare the full alias explicitly, name it `<driving>_all` by
convention, and never read the restricted driving alias in a CTE that
resolves rows beyond the entity being rebuilt. ADR-0008 makes alias scope a
declared, gate-checked property of derivation steps when the `derivation`
block lands — this section is the operational rule until then.

## 5. Definition of done (for a gold domain onboarding onto the shared tooling)

- [ ] Deploy workflow invokes only §3's sanctioned paths, with `--repo-root`
      (or `--domain`/`--master`/`--manifest`) pointing at its OWN tree.
- [ ] No Glue script upload, no gold Terraform apply, no UI build (§2).
- [ ] Any full-driving-table lookup declares a `_all` alias in manifest AND
      pipeline config (§4).
- [ ] Concurrency: per-domain deploy group, and the shared-gate condition of
      §2 acknowledged in the workflow's comments.
- [ ] `py -m pytest domains/<domain>/qe` green, plus product's suite green
      (the steward's gates protect the shared tooling).
- [ ] Nothing to do for Lake Formation access by consuming stacks — §6
      handles it automatically once the domain's first transform run has
      created its database.
- [ ] A `schedule.recompute` block may ship in the pipeline's INITIAL config:
      the leg never rides a first load, so there is nothing to arm around. It
      replaced `schedule.refresh` in UIFPE-457 (ADR-0020 decision 4) — the
      second sweep that block scheduled is gone, and with it the first-load
      race it had to guard against. If a pipeline still needs its first load
      started by hand — `aws glue start-job-run --job-name uif-echo-<env>-gold-transform
      --arguments '{"--gold_id":"<gold_id>"}'` with the pipeline's full-load
      `--worker-type`/`--number-of-workers`.

## 6. Gold databases carry the Lake Formation layer tag (no per-domain grants)

Other stacks read gold tables through Lake Formation — Beacon's Terraform,
for example, creates DESCRIBE/SELECT grants for its own roles on every gold
database it exports from. For that to work, the CI deploy role
(`echo-<env>-github-actions`) must itself hold those permissions *with grant
option* on the database. Granting that per domain meant an admin ask every
time a domain armed, so it is tag-based instead (LF-TBAC, set up in dev
2026-09-02):

- Every gold database carries the LF tag **`uif-echo-gold-layer={env}`**.
- The deploy role holds grantable DESCRIBE+SELECT against that *tag*, not
  against any database by name — so a newly tagged database is covered with
  zero further grants.
- **Tagging is folded into the arming process**: `sync_pipelines.py` ensures
  the tag on every managed pipeline's gold database on every sync. The
  database is created by the transform's first run, so a brand-new domain's
  tag lands on the first sync after that run (dev syncs on every merge). No
  one has to remember it.
- Tag problems WARN in the sync output and never fail a deploy — an untagged
  database only blocks the *consuming* stack's Terraform, which refuses with
  its own readable error.

One-time setup per account/environment (dev is done; uat/prod when they arm
— until then their syncs print a WARN and continue):

```bash
aws lakeformation create-lf-tag --tag-key uif-echo-gold-layer --tag-values dev uat prod
# authorize the deploy role to apply the tag…
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::<account>:role/echo-<env>-github-actions \
  --resource '{"LFTag":{"TagKey":"uif-echo-gold-layer","TagValues":["<env>"]}}' \
  --permissions DESCRIBE ASSOCIATE
# …and to pass table access on to consuming stacks' roles
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::<account>:role/echo-<env>-github-actions \
  --resource '{"LFTagPolicy":{"ResourceType":"TABLE","Expression":[{"TagKey":"uif-echo-gold-layer","TagValues":["<env>"]}]}}' \
  --permissions DESCRIBE SELECT --permissions-with-grant-option DESCRIBE SELECT
```

The gold jobs also READ Silver, and the databases they read carry the same
shape of tag so the grant is one rule rather than a list. Same two steps,
one key along:

```bash
aws lakeformation create-lf-tag --tag-key uif-echo-silver-layer --tag-values dev uat prod
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::<account>:role/echo-<env>-github-actions \
  --resource '{"LFTag":{"TagKey":"uif-echo-silver-layer","TagValues":["<env>"]}}' \
  --permissions DESCRIBE ASSOCIATE
```

Which Silver databases carry it, and the executor's read grant on them, are
Terraform's (`terraform-gold/silver_source_access.tf`) — unlike the gold tag,
no sync applies it.

The deploy role's IAM side (`lakeformation:GetResourceLFTags` +
`AddLFTagsToResource`) is tracked in
[`capabilities/infrastructure/echo-dev-deploy-2.policy.json`](../infrastructure/echo-dev-deploy-2.policy.json).

## 7. Null sentinels are declared once in the manifest (UIFPE-268)

Oracle PDH fills an attribute it has no value for with a placeholder rather
than NULL — `TBD` and `N/A` on text, `99998` and `99999` on numbers. Measured
on product in dev (6.83M rows, 2026-09-07): brand carries one on 5.49M rows,
durationUnit on 5.76M, finalPageCount on 4.46M. Consumers should see NULL,
and no domain should hand-write a `nullif` around every one of its source
columns to get there.

The mapping manifest declares the set once:

```yaml
null_sentinels:
  string:  ["TBD", "N/A"]
  numeric: [99998, 99999]
  exclude_columns: []        # optional, on top of the built-in protection
```

and the shared transform applies it **at the Silver view boundary**: every
source it registers for the mapping SQL (`register_full_views`, the Phase 2
driving view, the refresh and rebuild paths) is re-projected with each string
column wrapped against the string list and each numeric column against the
numeric list. The mapping SQL is untouched and cannot tell the difference,
which is the point — scalars, conditional branches, nested arrays and struct
members are all clean, and a second domain adopts the rule by adding the
block. Phase 1 key detection reads the tables directly and never sees it.

Protected, never wrapped: every column named in the source's own contract —
its `key_mapping` and `sot_pk_mapping` expressions, its `sot_filter`, its
watermark column — plus column names ending `_ID` or `_NUM` (`ATTR_GROUP_ID`,
`ITEM_CATALOG_GROUP_ID`, `ITEM_NUM`), Silver metadata (leading underscore),
and `exclude_columns`. The contract-derived protection is what keeps a
string key safe (price keys on `PPID`, stock on `PPID` + `ORGANIZATION_CODE`):
a cleaned key would hide the row from Phase 2's key filter and from
reconcile's anti-join. `exclude_columns` takes bare names (every source) or
`alias.COLUMN` (that source only — `SEGMENT1` is the ppid on `items` but a
category *code* on `categories_b`). Only kinds with declared values are
touched — a string-only declaration leaves numbers alone.

Two consequences to plan for:

- **Turning it on (or changing the set) moves the canonical hash on most
  rows.** Reload with a `--rebuild` (no change-log rows; consumers resync).
  An emit-mode `--refresh_columns` is only equivalent if it names EVERY
  non-key property: since UIFPE-269 the refresh hashes a COMPOSITE row
  (fresh named columns, Gold's stored values for the rest), so a partial
  list is no longer dangerous — but it leaves the un-named columns carrying
  sentinels until a Silver change or a rebuild reaches them. A silent deploy
  would make the next incremental see every touched row as changed.
- **Array elements are not dropped by the rule.** A nested row whose every
  field was a sentinel becomes a struct of NULLs; the mapping SQL's
  `collect_list` CTE decides whether to keep it (product's enablingTechnology
  filters them out). The rule is about values, not rows.

`validate_gold_schemas.py` lints the block in CI by calling the same
`gold_cdc.validate_null_sentinels` the runtime uses. **Declare it after the
`schema:` key** — the intake compiler appends lineage entries by text
position just before `schema:`, and the lint fails a block placed above it.
Anywhere below is fine; the compiler's `output_columns` append is bounded to
its own section.

The block is not yet part of the intake / wizard contract: today it is
authored in the manifest by hand, and a new entity scaffolded by the wizard
ships without it (retrofitting later costs a `--rebuild`). Adding a
top-level intake field and emitting it from the scaffolder is the follow-up.

## 8. The metadata columns stay last (UIFPE-268)

`_channel_hashes`, `_gold_updated_at`, `_gold_run_id` are appended by the
runtime after the manifest's `output_columns` on every CTAS / rebuild. The
maintenance auto-DDL used to `ADD COLUMNS`, which appends — so a business
column added after the first load landed *behind* them (product's
`categories`, 2026-08-31, position 59 behind 56–58). The maintenance path now
adds each new column `AFTER` the last business column, chaining when several
arrive together, so the three stay last however many columns a table grows.
Existing columns are never moved by that path; a table already carrying a
stray column behind the metadata is put right by its next `--rebuild`.
