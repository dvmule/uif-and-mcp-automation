# product / src/apps

This domain's own application code: pipeline configuration and its serving
surfaces.

| Path | What |
|---|---|
| `pipelines/` | Config-as-code for the gold pipelines: `base/{gold_id}.yaml` + `overlays/{env}/{gold_id}.yaml`, synced to DynamoDB by `stacks/echo/src/apps/cli/sync_pipelines.py` |
| `lambda_marketing_copy_sync/`, `lambda_marketing_copy_api/` | The marketing-copy serving copy and its read API — consumers of gold, built for this domain |

The gold runtime, dispatcher, control-plane CLIs and Transform UI moved to
`stacks/echo` at UIFPE-358 phase 2 — see ADR-0018.
