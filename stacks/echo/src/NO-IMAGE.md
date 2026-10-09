# Echo ships zero container images

The root `CLAUDE.md` rule-3 contract is *one domain builds ONE container image*.
Echo does not build one, and this file is the declared exemption
`.github/workflows/stacks-ci.yml` looks for in place of `src/Dockerfile`.

## What it ships instead

| Artefact | How it is packaged | Where it runs |
|---|---|---|
| 13 Glue jobs (`src/apps/pipeline/`) | `.py` scripts synced to `s3://uif-echo-dev-data/code/` | AWS Glue 5.1 (one job on 4.0, one Python Shell on 3.0) |
| `dq-kinds/` handlers | individual `.py` via `--extra-py-files` | inside the DQ Glue job |
| 5 Lambdas (`src/apps/{control_panel,lambdas}/`) | zips built by Terraform `archive_file` | Lambda, `python3.11` |
| 4 Lambda layers | zips staged in `infrastructure/deploy/layers/` | attached to the Lambdas |
| Control-panel UI (`ui/`) | Vite static build | S3 + CloudFront |

## Why not an image

Not a preference — Glue and Lambda are the runtimes, and neither takes an image
here:

- **Glue** runs a `.py` from S3, referenced by `ScriptLocation`. Glue *can* take
  a custom image, but only on Glue on ECS/EMR, not the serverless job type all 13
  of these use.
- **Lambda** takes a zip or an image. A zip is correct for functions this size,
  and the Oracle Instant Client layer is 127 MB — an image would have to carry it
  in every function rather than share one layer across three.

Adding a `Dockerfile` would produce an artefact nothing builds, nothing pushes,
and nothing deploys. A file that lies about the build contract is worse than a
check that fails.

## The real contract, in place of the image

- **`terraform apply`** creates every resource. `infrastructure/deploy/uif-echo-infra-deploy.sh`
  wraps it.
- **The Glue script sync is not optional.** Terraform tracks those scripts with
  `filemd5()` but does **not** upload them, so a deploy without the sync leaves
  the jobs running whatever was last pushed by hand.
- **Lambda code** is zipped by Terraform from `src/`; layers come from
  `infrastructure/deploy/layers/` — see the README there, and note the Oracle
  client is a licensed download that cannot be rebuilt from this repo.

## Same position as Beacon

Beacon reached this conclusion first and recorded it in its own `CHECKLIST.md`:
*"The rule-3 deviation (container images) is CLOSED — this stack ships zero
images."* Its API ships as a Lambda zip and its transform as a Glue script plus
engine zip.

Both stacks failed this gate until it was taught to read a declaration. And the
gate turns out never to have gated anything: `Stacks CI` is
**`disabled_manually`** — created 2026-08-28, one run that morning against
`stacks/harbor` on a scratch branch, disabled 53 minutes later, never run since.
So the amendment is not what unblocks anything today; it is what makes the gate
correct *before* someone re-enables it. Tracked as decision 4 in **ADR-0015**,
along with whether it should be re-enabled at all.

## When this file should be deleted

If Echo ever grows a component that genuinely needs a container — a long-running
API on ECS, say, as Beacon once planned — add `src/Dockerfile` for it and delete
this file. The gate prefers the Dockerfile when both exist, but leaving both
would misdescribe the stack.
