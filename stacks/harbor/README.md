# harbor

RELAY's import module — the counterpart to Beacon's export. A partner or
source system delivers data via SFTP or API; Harbor pulls it, transforms it
against a declared ingest template, and routes the result into the Pearson
systems that need it (a domain's Silver intake, a downstream API, or both).

**This is a deployable stack, not a domain.** It lives at `stacks/harbor/`,
not `domains/harbor/`, deliberately. `harbor`, `beacon`, and (once migrated)
`echo` are RELAY's three application stacks — each independently deployable,
each rebuilding its own API + Event + Schema Registry integration on every
release, each a full vertical slice (compute, infra, UI) rather than a
single small service. Domains (`product`, `customer`, `tax`, …) are
schema/config contributors that plug into whichever stack needs them,
nested under `stacks/<name>/domains/<domain>/` only when that stack
actually has real import/export/replicate work for that domain — never as
a placeholder.

## Why this exists as its own top-level folder

Found during the UIFPE-221 validate-branch review: `beacon` and `product`
currently sit under `domains/` even though both are deployable
stacks/platform-hosts, not schema-owning domains — indistinguishable in the
folder tree from `tax` (4 files, no code). Harbor is greenfield, so it's the
first chance to build the shape correctly instead of repeating that
conflation. Beacon migrates into this same shape later (it has live deployed
Terraform state, so it moves last, deliberately — see UIFPE-222's
description for the planned sequencing).

## Status

**Scaffold only (UIFPE-222).** No import job exists yet for any domain — no
partner feed has been named, no ingest template written. `src/`, `qe/`,
`infrastructure/`, and `ui/` each carry a `README.md` describing the
convention they'll hold real content under; none of them carry code yet.
This is deliberate — per this repo's own rule against empty scaffolding "for
completeness," nothing below gets built until a real Story names a partner
feed and a target domain.

## Component map (target shape, not yet built)

| Piece | Where | Runs as |
|---|---|---|
| Control-panel API | `src/apps/control_panel/` | ECS Fargate service, mirrors Beacon's shape |
| Import pipeline | `src/apps/pipeline/` | ECS Fargate task or managed Glue job, launched per batch |
| Delivery/record/emit executors | `src/apps/lambdas/` | Lambda functions |
| Ingest-template engine | `src/libs/` | library — imported by the pipeline, never deployed alone |
| Web UI | `ui/` | S3 + CloudFront, same shell as Beacon's control panel |
| API contract | `src/api/openapi.yaml` | registered contract (contract-first) |
| Domain-specific import configs | `domains/<domain>/` | only nested here once a real import job exists for that domain |

Note: `src/apps/` here means the same thing it means in every domain and
stack today — the individual runnable programs *inside* this stack. The
stack itself (`stacks/harbor/`) is the composite of all of them plus infra
and UI; it is not itself "an app."

## Working on this stack

Nothing runs yet. See `CHECKLIST.md` for what "done" looks like before this
section gets real commands in it.

## Ownership (Four-in-a-Box)

Not yet assigned — no FDE/UX/QE/PM has been named for Harbor. Assign before
any real code lands, per the same model every domain and stack uses.
