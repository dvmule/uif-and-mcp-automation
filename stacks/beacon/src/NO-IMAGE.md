# Beacon ships zero container images

This is the declared exemption `.github/workflows/stacks-ci.yml` looks for in
place of `src/Dockerfile`, per the root `CLAUDE.md` rule-3 contract.

**Beacon decided this itself, and this file only makes the existing decision
machine-readable.** From `../CHECKLIST.md`:

> **The rule-3 deviation (container images) is CLOSED — this stack ships zero
> images.** `src/Dockerfile.api` went at UIFPE-226's probation exit

and, on the same list:

> - [x] ★ No container images: the API ships as a Lambda zip, the transform as a
>   Glue script + engine zip (see note above).

So the Dockerfile was not forgotten — it **existed and was deliberately removed**
once the API proved it ran as a Lambda zip. `src/apps/beacon_export/fargate_entrypoint.py`
keeps its name from the same era, and says so in its own header: the ECS Fargate
path was deleted at UIFPE-215's probation exit, and since UIFPE-209 its only
caller is `glue_entrypoint.py`.

## Why this file exists at all

The gate could not read a prose declaration in `CHECKLIST.md`, so it failed
Beacon on a deviation Beacon had already closed. Nobody noticed for a blunter
reason than path filters or branch timing: **`Stacks CI` is
`disabled_manually`.** It was created 2026-08-28, ran once that morning against
`stacks/harbor` on a scratch branch, and was disabled 53 minutes later. It has
not run since. Whether it was switched off *because* it failed on Beacon is
inference, not record.

Echo hit the same gate first, being the next stack to land real code under
`src/`, which is what surfaced it. Tracked as decision 4 in **ADR-0015**.

## One clarification, so this is not read too broadly

`infrastructure/terraform/keeper-gateway.tf` does define a container workload —
the account Keeper gateway, adapted from the cybersecurity team's repo and gated
off behind `keeper_gateway_enabled`. That runs a **vendor image Beacon does not
build**, which is why it is not a build contract and does not need a
`src/Dockerfile`. "Zero images" means zero images *built from this source tree*.

## Authorship

Written under **UIFPE-355** (Echo's migration) by the engineer fixing the shared
gate, not by Beacon's owner. It asserts nothing new about Beacon — every claim
above is quoted from Beacon's own `CHECKLIST.md` and source comments. **Beacon's
owner should confirm it, and is welcome to reword it.** If Beacon ever builds an
image again, add `src/Dockerfile` and delete this file; the gate prefers the
Dockerfile when both are present.
