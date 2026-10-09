# Docker — setup, standards & security (the container how-to)

The container image is the **one build/deploy contract** in UIF: every domain builds exactly one image from `domains/<domain>/src/Dockerfile`, and that image — not the source — is what CI scans, signs, and CD promotes. This guide is the practical, do-this companion to `guidelines.md`  for the image specifically: how an FDE writes the Dockerfile, builds it, hardens it, scans and signs it, and hands it to Kubernetes ([`kubernetes.md`](kubernetes.md)).

Grounding: OCI Image & Distribution Spec, the Dockerfile reference, the CIS Docker Benchmark, and NIST SP 800-190. These are public — anyone can verify the rules below.

## Set up your machine

Install a build engine (Docker Engine / BuildKit, or a rootless equivalent like Podman/Colima) and the supply-chain CLIs used by the pipeline so you can run the same checks locally:
```
docker buildx version     # BuildKit builder
hadolint --version        # Dockerfile linter
trivy --version           # vulnerability + config scanner
syft version              # SBOM generator
cosign version            # image signing (Sigstore)
```
The pipeline (`../delivery/`) runs all of these; running them locally means no surprises in CI.

## The standard Dockerfile (multi-stage, non-root, minimal)

A worked, portable shape. Adapt the stages to the language; keep every rule.
```dockerfile
# 1) Build stage — full toolchain, never shipped.
FROM node:20-bookworm@sha256:<pinned-digest> AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# 2) Runtime stage — minimal base, pinned by digest, no build tools.
FROM gcr.io/distroless/nodejs20-debian12@sha256:<pinned-digest> AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules
USER nonroot                       # never root
EXPOSE 8080
HEALTHCHECK CMD ["/nodejs/bin/node", "dist/healthcheck.js"]
CMD ["dist/server.js"]
```
Rules this encodes:
- **Multi-stage** — the toolchain stays in `build`; the runtime image carries only the artifact.
- **Minimal/distroless base, pinned by `@sha256`** — smallest attack surface, reproducible.
- **`USER nonroot`** — the process never runs as root (pairs with `runAsNonRoot` in `kubernetes.md`).
- **One process, explicit `EXPOSE`, a `HEALTHCHECK`.**
- **No secrets, ever** — do not `COPY` `.env`, keys, or tokens; secrets arrive at runtime (`kubernetes.md`, External Secrets).

Add a **`.dockerignore`** so build context (and secrets, `.git`, `node_modules`) never enters a layer:
```
.git
node_modules
**/.env*
**/*.log
```

## Build, tag, scan, sign, push

Tags are immutable and carry both the SemVer version and the git sha (`guidelines.md` §1):
```
IMAGE=<acct>.dkr.ecr.eu-west-1.amazonaws.com/product
VERSION=1.4.2
SHA=$(git rev-parse --short HEAD)

# build (BuildKit)
docker buildx build -f src/Dockerfile -t $IMAGE:$VERSION -t $IMAGE:sha-$SHA .

# lint (also runs in CI)
hadolint src/Dockerfile

# scan — fail on HIGH/CRITICAL
trivy image --severity HIGH,CRITICAL --exit-code 1 $IMAGE:$VERSION

# SBOM (attach to the image)
syft $IMAGE:$VERSION -o spdx-json > sbom.spdx.json

# push, then sign the pushed digest (Sigstore)
docker push $IMAGE:$VERSION && docker push $IMAGE:sha-$SHA
cosign sign $IMAGE:$VERSION
```
In the cluster, admission policy (Kyverno/Gatekeeper) **verifies the cosign signature** so an unsigned image cannot run — the enforcement side of `guidelines.md` §4.

## Security checklist (before you push)

- [ ] Base image minimal/distroless and pinned by `@sha256`.
- [ ] Runtime stage carries no compilers, package managers, or shells you don't need.
- [ ] `USER` is non-root; no `--privileged` assumptions.
- [ ] No secret, key, or `.env` in any layer (`docker history` shows nothing sensitive).
- [ ] `.dockerignore` excludes `.git`, deps, env files, logs.
- [ ] `trivy image` clean of HIGH/CRITICAL; SBOM generated; image `cosign`-signed.
- [ ] One process; `HEALTHCHECK` present; `EXPOSE` matches the app port.

## Where it goes next

The image is referenced by the domain's Helm chart (`domains/<domain>/infrastructure/helm/values.yaml` → `image.repository` + `image.tag`) and deployed to Kubernetes. Continue in [`kubernetes.md`](kubernetes.md). CI/CD that runs this end to end: `../delivery/`. Runnable Dockerfiles that follow this shape: `../../delivery-lab/reference-implementations/api/developer-registration-api/Dockerfile` (Node) and `../../delivery-lab/reference-implementations/api/developer-registration-api-fastapi/Dockerfile` (Python).
