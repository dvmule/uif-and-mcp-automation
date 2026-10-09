# Base image is a build ARG, not a literal, so that promoting this image from
# the public Alpine base -> our ECR mirror -> the SIF registry is a build-arg
# change rather than a file edit. DEPLOY-DEV.md "Phase 7" is that promotion,
# and it is DONE, not pending: both IAM prerequisites it named are closed
# (ecr:GetDownloadUrlForLayer granted on uif-apicurio-dev-deploy-policy,
# applied live 2026-09-15) and the mirror was pushed 2026-09-14, confirmed
# still live via `aws ecr describe-images` on 2026-09-15 and built/run-tested
# against directly (non-root, /docs returns 200) before this default changed.
#
# That repository name is not free-form: the deploy role's ECR statement is
# scoped to arn:aws:ecr:eu-west-1:283556840805:repository/uif-apicurio-dev-*,
# and .claude/hooks/validate_naming.py rejects a nested `.../python` form
# outright.
#
# An ARG declared before the first FROM is in scope for every FROM line, which
# is why this one sits above both stages and is re-declared inside neither.
#
# ON THE DIGEST PIN. capabilities/infrastructure/docker.md:85 requires the base
# be pinned by @sha256. A digest identifies content, but a *reference* is
# registry+repository+digest, and the same bytes re-pushed to another registry
# keep the digest while the full reference changes. So one ARG cannot hold a
# digest valid for both the public image and the mirror -- and this is why the
# digest below is NOT the one DEPLOY-DEV.md's own Phase 7 table lists for
# 3.12-alpine3.24 (sha256:7c499675...): that value is what SIF's PORTAL
# reports for the amd64 manifest, not what `docker push` actually wrote to our
# ECR. `docker tag` + `push` reconstructs a single-arch manifest rather than
# preserving the original bytes, so only the push output is an authoritative
# digest -- confirmed here by pulling this exact reference and building
# against it, not by trusting the portal or DEPLOY-DEV.md's table.
#
# WHY ALPINE AND NOT python:3.12-slim. The Debian base carried 205 HIGH/CRITICAL
# CVEs (1 CRITICAL, 204 HIGH): 203 from Debian OS packages -- 143 of those in
# linux-libc-dev alone -- and only 7 fixable at all. It was not patchable, the
# base had to change. This image scans 0 HIGH/CRITICAL and is 126 MB against the
# Debian image's 584 MB.
#
# NOT "all SIF images are Alpine/musl" -- that claim was here and is false. The
# SIF Python library carries 56 tags: 34 Alpine, 15 slim, 3 bookworm, 3
# bullseye, 1 amazonlinux2023 (read off the portal 2026-09-14). Alpine is a
# CHOICE this stack made for the CVE numbers above, not the only option SIF
# offers, and not why this now points at SIF instead of Docker Hub -- it moves
# to SIF because SIF re-scans with Twistlock and gives curated provenance, not
# because SIF forces Alpine. See qe/ci.sh's `trivy image` gate, which scans
# for exactly this and did not exist before -- the old gate ran `trivy config`
# twice and never once looked at the built image, which is how 205 stayed
# invisible.
ARG BASE_IMAGE=283556840805.dkr.ecr.eu-west-1.amazonaws.com/uif-apicurio-dev-base-python-ecr-repo:3.12-alpine3.24@sha256:14f65fb17e64d24fedd69a30d41c19f17d4bbcfb3a23b99663858e65c44616f6

# NOTE ON THE `# hadolint ignore=` LINES BELOW. Each one must be the LAST
# comment line before the instruction it excuses, with its prose reasoning
# ABOVE it. With prose after the directive, hadolint silently does not apply
# it and the file reads as though it had -- the same trap qe/ci.sh documents
# for `# trivy:ignore:` directives. Verified by running hadolint both ways.

# ── builder ───────────────────────────────────────────────────────────────────
# Exists so the C/Rust toolchain below never reaches the runtime image. Every
# dependency here resolved to a prebuilt musllinux wheel (including the Rust
# ones, pydantic-core and watchfiles), so nothing actually compiles today --
# but the toolchain stays because the moment one dependency bumps to a version
# that has no musllinux wheel for this interpreter, pip falls back to building
# from source, and without these packages that is a build failure rather than a
# slow build.
# DL3029 says do not use --platform with FROM. Ignored deliberately: the
# Pearson pipeline standard REQUIRES it ("Add --platform=linux/amd64 to FROM
# lines as a permanent guard") because Actions runners are amd64 while an
# Apple Silicon laptop pulls arm64 by default, and an arm64 image pushed to
# ECR fails at Fargate task start. hadolint's general advice loses to the
# platform's explicit rule here. BuildKit emits the same complaint as
# FromPlatformFlagConstDisallowed; it is a lint warning, not a build error.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 ${BASE_IMAGE} AS builder

WORKDIR /app

# build-base/python3-dev/libffi-dev are the musl equivalents of Debian's
# build-essential -- there is no apt and no gcc metapackage here.
# DL3018 wants apk versions pinned. Not here: these are build-stage-only
# packages that never reach the runtime image, so pinning them buys no
# reproducibility a consumer can observe, and a stale pin becomes a build
# failure the day Alpine drops that exact -rN revision.
# hadolint ignore=DL3018
RUN apk add --no-cache build-base python3-dev libffi-dev

# COPY before pip install: the setuptools build backend needs the source tree
# present to prepare wheel metadata for this project itself.
COPY pyproject.toml ./
COPY app ./app

# --prefix=/install puts the whole install (bin/ + lib/pythonX.Y/site-packages)
# under one directory that the runtime stage can take as a single COPY. The
# runtime base has its Python at /usr/local, which is what /install overlays.
#
# pip/setuptools are upgraded in the BUILDER only. Only --prefix=/install is
# copied forward, so the builder's own pip and setuptools never reach runtime.
#
# On the two Debian findings that were not OS packages, because the obvious
# reading of them is wrong and someone will otherwise "re-fix" them here.
# Trivy reported setuptools 70.3.0 (CVE-2025-47273) and msgpack 1.1.2
# (GHSA-6v7p-g79w-8964) against the Debian image. NEITHER was an installed
# package. `pip install --upgrade pip` had pulled pip 26.2.1, which ships a
# CycloneDX SBOM of its own vendored libraries at pip/_vendor/bom.cdx.json
# listing exactly `pkg:pypi/setuptools@70.3.0` and `pkg:pypi/msgpack@1.1.2`;
# trivy read that third-party SBOM and reported its contents as image packages.
# (The baseline scan even warned "Third-party SBOM may lead to inaccurate
# vulnerability detection".) setuptools was never on that image's filesystem at
# all -- `find / -iname "*setuptools*"` returned nothing -- and msgpack was only
# pip's vendored copy, not importable and not in pyproject.toml.
#
# So those two were artifacts of pip's vendored SBOM, not of our dependency set,
# and `setuptools>=78.1.1` below is not what cleared them. They are absent now
# because this runtime base ships pip 25.0.1, which carries no bom.cdx.json for
# trivy to read. The pin stays anyway: it costs nothing, and it makes the
# builder's actual build backend a patched one rather than whatever pip resolves.
# Do not conclude from this that the runtime stage has no pip -- it does, from
# the base image (/usr/local/bin/pip). Removing it was not necessary to reach
# zero and is not attempted here.
# DL3013 wants == pins. `setuptools>=78.1.1` is a security FLOOR on purpose --
# the point is to be at or above the CVE-2025-47273 fix, not to freeze on one
# release. The application's own dependency versions are pinned where they
# belong, in pyproject.toml/uv.lock, not restated here.
# hadolint ignore=DL3013
RUN pip install --no-cache-dir --upgrade pip 'setuptools>=78.1.1' \
    && pip install --no-cache-dir --prefix=/install .

# ── runtime ───────────────────────────────────────────────────────────────────
# DL3029 says do not use --platform with FROM. Ignored deliberately: the
# Pearson pipeline standard REQUIRES it ("Add --platform=linux/amd64 to FROM
# lines as a permanent guard") because Actions runners are amd64 while an
# Apple Silicon laptop pulls arm64 by default, and an arm64 image pushed to
# ECR fails at Fargate task start. hadolint's general advice loses to the
# platform's explicit rule here. BuildKit emits the same complaint as
# FromPlatformFlagConstDisallowed; it is a lint warning, not a build error.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 ${BASE_IMAGE}

WORKDIR /app

# PYTHONDONTWRITEBYTECODE: the app code under /app/app stays root-owned and
# read-only to the runtime user (below), so a CPython attempt to drop
# __pycache__/*.pyc beside a module would hit EACCES. Disabling bytecode
# writes removes the attempt entirely rather than relying on CPython's
# silent-fallback behaviour.
# PYTHONUNBUFFERED: stdout reaches the awslogs driver per-line instead of
# sitting in a 4 KB buffer (logConfiguration in infrastructure/terraform/bff.tf).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# `apk upgrade` patches the OS packages the pinned base was built with. It is
# what actually takes this image to zero: the unpatched Alpine base carried 7
# HIGH (all in libuuid 2.42.1-r0, CVE-2026-53612/53613/53614/76642/78408/78409/
# 78410), and all 7 were marked fixable in 2.42.3-r0/-r1. So this is a real
# package upgrade, NOT a scanner suppression -- there is no .trivyignore and no
# severity threshold raised anywhere for this image.
#
# The cost is honest: `apk upgrade` resolves against Alpine's live repositories,
# so the digest-pinned base no longer implies a byte-identical output image --
# two builds a month apart can differ. That is the deliberate trade. Pinning the
# base by digest and then never patching it is what produced the 205 in the
# first place; this keeps the base pinned for reproducible *inputs* and patches
# forward for CVEs. Prefer the plain `apk upgrade` to a pinned
# `apk add "libuuid>=2.42.3-r1"`: the pin would fix exactly today's seven and
# silently do nothing about the next package to be flagged.
#
# curl IS kept, and that was measured rather than assumed. curl exists for the
# ECS task-definition health check (CMD-SHELL curl -f
# http://localhost:8000/api/v1/health, infrastructure/terraform/bff.tf). The
# worry was that libcurl's transitive TLS stack would reintroduce the runtime
# surface that produced the Debian base's 203 -- on Debian, curl and
# libcurl4t64 were 8 of those 203. Built and scanned both ways on this base, it
# does not: with curl and without curl are BOTH 7 HIGH before the upgrade and
# BOTH 0 after it, so curl's CVE cost here is zero and its only cost is ~6 MB
# (120 MB -> 126 MB). Since it is free in CVE terms, it stays, and the ECS
# health check keeps working unchanged -- no terraform edit is needed. Re-measure
# before removing it; do not remove it on the general principle that curl is
# attack surface, because on this base that principle is not what the numbers say.
# DL3018 wants `apk add curl=<version>`. Pinning here would defeat the line's
# entire purpose: this RUN exists to pull the NEWEST patched packages, and a
# pinned curl would freeze it at today's CVE state and silently rot.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache && apk add --no-cache curl

# Runtime user. Fixed, explicit, non-zero UID/GID rather than letting the tool
# pick one: the kernel enforces the *number*, not the name, and the name never
# leaves this image. ECS task definitions and Kubernetes `runAsUser` /
# `runAsNonRoot` are expressed numerically, and ownership on any mounted volume
# is numeric too. An auto-assigned UID depends on which accounts the base
# image's packages happened to create first, so a base-image bump could
# silently renumber appuser and break those. 10001 is also clear of the
# distro's auto range, which collides with the first host user under a bind
# mount.
#
# This is busybox adduser/addgroup, not shadow-utils: there is no `useradd` on
# musl, and the flags are not Debian's. -S means "system account", which on
# busybox allocates from the system range (<1000) unless an explicit id is
# given -- so -u/-g are what actually hold this at 10001, and -S alone would
# silently hand out something else. Unlike Debian's `useradd --uid 10001`,
# busybox does not warn that the id exceeds SYS_UID_MAX. -D skips password
# assignment, -H skips home-directory creation (the app needs no home).
# `adduser -S appuser -G appgroup` from the pipeline-patterns example is the
# same call without the explicit numbers; the numbers are the point here.
RUN addgroup -g 10001 -S appuser \
    && adduser -S -D -H -u 10001 -G appuser -s /sbin/nologin appuser

# Deliberately BEFORE the USER switch and with no `COPY --chown`: /app/app and
# site-packages stay root-owned so the process cannot rewrite its own code at
# runtime. It only needs read+execute, which it has.
COPY --from=builder /install /usr/local
COPY app ./app

# The one path the app writes at runtime. app/services/delivery_lab_service.py
# put_table_config() writes cdc_tables_path (/app/config/enrollment.json)
# whenever CDC_TABLES_SSM_PARAMETER is empty -- and that terraform variable
# defaults to "" (infrastructure/terraform/variables.tf), so the file branch is
# reachable in a real deploy, not just locally. The directory did not exist in
# this image before, so that write already failed as root (ENOENT); creating it
# owned by appuser means switching to non-root is strictly no worse than root
# here, instead of trading ENOENT for EACCES.
RUN install -d -o appuser -g appuser -m 0755 /app/config

# No HEALTHCHECK on purpose. The health check is defined once, in the ECS task
# definition (infrastructure/terraform/bff.tf), which is the definition Fargate
# actually acts on -- a container-level HEALTHCHECK here would be inert in the
# deployed path and a second copy of a value that has already been wrong once
# (see the port 9000 / path comment in ecs.tf). Trivy DS-0026 flags its absence
# at LOW, below the HIGH,CRITICAL gate. This diverges from the HEALTHCHECK line
# in capabilities/infrastructure/docker.md, which is written for that doc's
# Helm/Kubernetes delivery path; revisit if this stack moves to Kubernetes.

# Numeric, not `USER appuser`, for the same reason the uid is fixed above: the
# kernel enforces the number and ECS/Kubernetes runAsUser is numeric, so
# stating the number here removes any dependence on /etc/passwd resolving the
# name at container start. Equivalent today (appuser IS 10001) and what
# hadolint DL3066 asks for.
USER 10001:10001

# Unprivileged port (>1024), so binding it as appuser needs no capability.
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
