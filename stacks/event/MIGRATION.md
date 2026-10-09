# Event stack migration — adopting the live dev resources from domains/product

One-time, per environment (dev first). The six DynamoDB tables hold **live
data** (subscriptions, run + publish + delivery metrics), so everything is
**moved between Terraform states, never recreated**: the domain releases
with `removed { destroy = false }`, the stack adopts with `import` blocks.
Until both applies land, AWS is untouched — the pipeline keeps running on
the deployed resources throughout.

## Order matters

1. **Merge the PR.** Both workflows will fire; the safe order is enforced by
   doing the applies deliberately (leave the event stack's deploy latch off
   for the first run if both would auto-apply).

2. **Domain apply first** (`deploy-product-dev.yml` / `make apply ENV=dev`
   in `domains/product/infrastructure/terraform`). This:
   - executes the `moved` blocks (bus, DLQ, alarm, recorder ESM, mapping
     upload → `module.product_events`; the mapping object is then replaced
     in place onto its new key `event-pipeline/domains/product/…`);
   - executes the `removed { destroy = false }` blocks — the platform
     resources leave the domain state, AWS untouched;
   - updates the pipeline-configs seed row (new `gold` +
     `processor.arguments` sections — the shared controller/job read these);
   - uploads `product.subscriptions.seed.json` (bootstrap seeds are domain
     data now, not stack code).

   The module's `data.aws_lambda_function.dlq_recorder` lookup resolves the
   already-deployed Lambda, so the recorder hook keeps working mid-move.

3. **Stack apply second** (`stacks/event/infrastructure/terraform`):

   ```bash
   terraform init -backend-config=backends/dev.s3.tfbackend
   terraform plan -var-file=envs/dev.tfvars
   ```

   The plan should show **imports** (imports.tf) plus in-place updates:
   - Glue job: new script location (`event-pipeline/glue/…`) and slimmed
     default arguments (per-domain args now ride the config record);
   - controller Lambda: slimmed environment (no GLUE_DATABASE/ATHENA_OUTPUT
     — read per domain from pipeline-configs), DLQ moves to the new
     `uif-event-dev-trigger-dlq`;
   - GoldMergeCompleted rule: domain filter dropped (the config table IS the
     allowlist — required for the no-stack-change-per-domain invariant);
   - IAM: wildcarded per-convention (gold DBs `uif_echo_{env}_gold_*`, buses
     `uif-event-{env}-*`, DLQs `…-*-dlq`, rules `rule/uif-event-{env}-*`);
   - provisioner: DOMAIN_BUS env var gone (bus derived per row).

   **Anything showing as destroy/replace on a DynamoDB table = stop.**

4. **The one CLI import** — the provisioner's DynamoDB-stream event source
   mapping is keyed by a runtime UUID, so it cannot be an import block:

   ```bash
   aws lambda list-event-source-mappings \
     --function-name uif-event-dev-subscription-provisioner \
     --query 'EventSourceMappings[0].UUID' --output text
   terraform import aws_lambda_event_source_mapping.subscription_provisioner <UUID>
   ```

   Then `terraform apply` the plan.

5. **Post-apply checks** (all live-verified behaviors, re-run them):
   - manual announcement → publish → delivery (the 1-row gold run
     `jr_dc5be6cf…` replay);
   - `PutEvents` a sample GoldMergeCompleted for `product` (dedupe skip in
     controller logs) and for an unseeded domain (clean config skip);
   - a `PENDING` flip on a subscription re-provisions against the derived
     bus name.

6. **Cleanup (later, deliberate):** the old S3 objects under
   `product-events/` (script, mapping, athena-results) are orphaned — delete
   after confirming nothing references them; prune the domain's old
   Terraform-generated `lambda:InvokeFunction` statement on the controller
   (superseded by the stack's); delete `imports.tf` right after dev's first
   successful apply — it is dev-only, and an import block whose resource does
   not exist fails the plan, so it must be gone BEFORE any uat/prod init
   (those environments create fresh; nothing to adopt).

## Rollback

Before the stack apply, nothing changed in AWS — revert the PR and the
domain state can re-adopt the resources with its own import blocks (the
mirror of imports.tf). After the stack apply, roll forward: the resources
are correctly owned, only addresses moved.
