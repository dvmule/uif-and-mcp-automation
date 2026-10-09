# 0019. Version at the Capability Boundary — a Release Manifest for Non-Container Build Artifacts

- Status: Accepted (2026-09-21)
- Date: 2026-09-18
- Author: satyanarayana.gunavanthan@pearson.com
- Deciders: satyanarayana.gunavanthan@pearson.com (framework owner) — approved 2026-09-21
- Scope: How Echo and Beacon's Glue scripts, Lambda functions and Lambda layers get an immutable, taggable build unit equivalent to a container's image digest, and the general versioning rule that follows from it.
- Related: UIFPE-456. Raised in the same conversation as, and not yet reconciled with, this repository's mono-repo-vs-poly-repo open question (see the CI/CD tab of the UIF Engineering Delivery Guide).

## Context

Raised by a senior engineer: this repository's shared `develop` branch lets a stack's untested changes ride along when a different stack promotes to production. Apicurio already solves this — `docker build` produces exactly one artifact, an image digest, and tagging is just naming that digest.

Echo and Beacon have no equivalent. Read directly from `stacks/echo/infrastructure/deploy/_uif-echo-deploy-common.sh` on `develop`, 2026-09-18:

- `uif_echo_sync_glue_scripts()` runs `aws s3 sync` into a fixed, shared S3 prefix (`jobs/` or `code/`), overwritten by every deploy, every environment, every engineer.
- `uif_echo_upload_layers()` uploads each Lambda layer zip to a fixed S3 key, then relies on `terraform apply -replace=<layer resource>` to force AWS to publish a new layer version.

The script's own comment names the gap explicitly: *"the complete fix is a versioned prefix per artifact (tracked in ADR-0019, not yet built)."* This ADR is that fix.

## Decision — the rule (settled, not a proposal)

**Version at the deployable capability boundary, not at the file/package boundary.** A capability's release tag (`echo/v1.4.3`) is the only unit any engineer, QE process, or Ops action ever consumes, gates on, or promotes. Individual artifacts stay internally immutable and independently versioned — that granularity never surfaces outside the build step. This governs every stack this repository builds, not only Echo and Beacon, and it holds regardless of how the mechanics below land. Tracked on `UIFPE-456`.

## Proposed mechanics — how Echo and Beacon satisfy the rule above

The rule is settled; the following is the engineering design for meeting it, open for review:

1. **Each artifact type keeps its own native AWS immutability primitive** — nothing bespoke is invented:
   - Lambda functions: `PublishVersion` → an immutable numbered version, with an alias (`prod`, `uat`) pointing at one.
   - Lambda layers: `PublishLayerVersion` → an immutable, auto-incrementing version.
   - Glue scripts: S3 object versioning — already `Enabled` on Echo's artifacts bucket (`aws_s3_bucket_versioning.artifacts`, `stacks/echo/infrastructure/terraform/s3.tf`).

2. **A release manifest ties one release tag to the exact native version, SHA256 and originating git commit of every artifact in that build.** Illustrative shape:

   ```yaml
   echo/v1.4.3:
     git_commit: 8f31a2c
     glue_scripts:
       bronze_ingest.py: { s3_version_id: "3HL4kqtJ8pXo...", sha256: "a1b2c3..." }
     lambdas:
       scheduler_dispatcher: { version: 7,  sha256: "d4e5f6..." }
     layers:
       gcs_layer: { version: 17, sha256: "0d1e2f..." }
   ```

3. **Any change to any single artifact bumps the whole release tag.** `echo/v1.4.3` failing QE becomes `echo/v1.4.4` — never a patched `v1.4.3`, and never a mixed release ("Lambda on v1.4.4, Glue still on v1.4.3"). Independent per-artifact versioning inside one tag namespace is exactly the ambiguity a release tag exists to remove.

4. **Promotion re-points environment config at the manifest for the target tag — nothing rebuilds.** A Lambda alias moves to a version number; a Glue job's script location moves to a version-pinned prefix; a layer ARN moves to a version. Same shape as flipping a container's tag to a new digest.

## What this does not decide

- **Whether a versioned S3 prefix (`jobs/v1.4.3/…`) is compatible with `.claude/rules/naming/00-core.md`**, which bans version tokens in resource names with one carved-out exception (EventBridge/SQS subscription names). A key path is not a resource name, but it is close enough to the banned pattern to need an explicit yes from whoever owns that rule — not assumed here.
- **Mono-repo vs. poly-repo.** A different answer to that senior engineer's original problem — splitting stacks into separate repositories so each release cycle is independent by construction — was raised at the same meeting with no recorded outcome. This ADR assumes mono-repo stays; that assumption is not a decision this document makes.
- **Whether Harbor adopts this model.** Harbor currently has zero infrastructure (UIFPE-222); this ADR has nothing to extend until that changes.

## Consequences

- `_uif-echo-deploy-common.sh`'s `uif_echo_sync_glue_scripts` and `uif_echo_upload_layers` need to write to versioned locations and emit a manifest entry, not sync/overwrite in place.
- QE and Ops tooling gate and promote against the release tag exclusively — any tooling or dashboard that currently reports per-artifact status needs to roll that up to the release tag before this can be adopted as the interface people actually use.
- The Build & Release tab of the UIF Engineering Delivery Guide loses its `proposed here` markers as each piece lands, the same convention already used for the rest of ADR-0019's promotion model.
- **Unblocks Apicurio's Half 2 (promotion identity) work under `UIFPE-456`.** `docs/superpowers/specs/2026-09-19-apicurio-release-identity-design.md` names *"ADR-0019 being accepted"* as its explicit blocking dependency for promotion, having already built Half 1 (build identity: `stacks/apicurio/VERSION`, the git tag, `scripts/release-manifest.sh`) independent of this decision. That work can now proceed.
