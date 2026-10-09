# Data — Outbound export standard (the worked standard)

How data leaves UIF. Any domain that delivers data to an external partner — a
file drop, a feed, an emailed extract — follows this standard, regardless of
which tool executes the export. The Data capability's other documents govern
data as an owned product *inside* the fabric; this one governs the moment a
copy of that product crosses the boundary, where a silent defect becomes a
partner incident. The worked implementation is the `beacon` stack
(RELAY Beacon — a deployable stack, not a schema-owning domain, per
ADR-0010); its design record is
[`stacks/beacon/docs/beacon-export-design.md`](../../stacks/beacon/docs/beacon-export-design.md),
cited below as D-numbers.

## 1. Contract first

- An export is a **registered, versioned config pair** — a mapping config
  (sources, field mappings, filters, validations) and an export config
  (rendering, packaging, delivery, gates) — validated before publish. Ad-hoc
  SQL against Gold is not an export. [D11, D22]
- Published config versions are **immutable**. A change is a new version;
  rollback is running an earlier version, never editing one. [D22]
- A source query may be *converted* into a mapping config by tooling, but is
  never executed during authoring. [D11, D12]

## 2. Approval & ownership

- **Four-eyes on every publish**: the author cannot approve their own export.
- **Destinations are author-owned; channel types are platform-owned.**
  Credentials are held in the secrets service and referenced by name — never
  stored in configs, registries, or logs. [D16]
- Every export names its owning domain; the owning domain keeps its published
  config pairs in its own `src/schemas/`.

## 3. Quality gates — fail closed

- Gates run **before** rendering. A breach aborts the batch: nothing reaches
  the destination, and the failure is recorded in the run history. [D4]
- Minimum gate set: `min_rows` and `max_rejection_pct`. Domains add
  entity-specific validations in the mapping config; rejected rows are
  quarantined with reasons, not dropped.

## 4. Delivery semantics

- **One output format per export.** The same data in another format is a
  separate export with its own gates, destination, and schedule. [D17]
- **Atomic delivery**: partners must never observe a partial file —
  tmp-then-rename on SFTP, content-first/manifest-last on S3.
- Every batch ships a **manifest with SHA-256 checksums**.
- **Batch-unique filenames** (`{batch_id}` mandatory in the pattern) so
  re-delivery is idempotent.

## 5. Runtime split

- Heavy transform runs as a containerized batch task; light steps (deliver,
  record, emit) run as functions; one orchestrator owns the flow and records
  every outcome — success and failure — in a queryable run history. [D20]
- Source schemas are read **live** via the governed catalog (Glue / Lake
  Formation): a new column in a source requires no registry edit and no
  deployment.

## 6. Definition of done (outbound export)

- [ ] Config pair validated by the platform's own validators before publish.
- [ ] Approved by someone other than the author.
- [ ] Gates configured and proven to abort on breach (test with a bad batch).
- [ ] Delivery verified atomic; manifest checksums verified end to end.
- [ ] Run history shows the export's successes AND failures.
- [ ] Owning domain's config pair committed under its `src/schemas/`.

**Worked implementation:** [`stacks/beacon/`](../../stacks/beacon/) —
self-service control panel, PySpark pipeline, SFTP/S3/email delivery. Domains
onboard by adding configs, not by importing its code (see
`domains/product/src/schemas/beacon/`).
