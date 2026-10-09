# product / infrastructure/terraform

Terraform for the `product` domain's own resources: event delivery
(`product_events*.tf`), the marketing-copy serving copy
(`marketing_copy_service.tf`) and the product API (`main.tf`). Capability
standards: `../../../../capabilities/infrastructure/`.

The gold runtime's Terraform — Glue jobs, config table, gold buckets,
dispatcher, Transform UI Lambdas — is `stacks/echo/infrastructure/terraform-gold`
([ADR-0018](../../../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md)).

**Apply discipline, per environment.**

- **dev — cutover DONE (2026-08-04), and `apply` is AUTOMATED.**
  `deploy-product-dev.yml` runs `make init/plan/apply ENV=dev` on every push to
  `develop`, unattended. Do not apply dev by hand: it races the workflow.
  Reviewing `make plan ENV=dev` before you merge is the only human gate there is
  — a plan that replaces or destroys a resource will do so at merge time, so read
  it first and merge away from the schedule's ticks
  (see `../../CLAUDE.md` → Gotchas).
- **uat / prod — `apply` is yours.** uat has a real backend and a
  `overlays/uat` set as of 2026-09-28, so it is gated only by the
  `PRODUCT_DEPLOY_ENABLED` latch and the `uat` environment approval — but note
  this root has never been applied there, so the first plan is a CREATE of its
  ~25 resources, not a no-op. prod is still blocked by a placeholder backend
  and has no overlay.

- State key: `echo-gold/{env}` for dev; uat keeps its state inside the stack's
  own data bucket (`uif-echo-uat-data`, `tfstate/product-domain-terraform.tfstate`)
  so a bucket policy cannot reach across stacks. Backends in `backends/`,
  per-env vars in `envs/`.
- This domain's own resources still carry inherited `echo-{env}-*` names; their
  rename is a separate, planned change.
- Env picklist is read from `stacks/echo/src/apps/environments.json`.

```bash
make init  ENV=dev
make plan  ENV=dev   # review this BEFORE merging — the merge applies it
# make apply ENV=dev # dev only: the workflow owns this; running it here races it
```
