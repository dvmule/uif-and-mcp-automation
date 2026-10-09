# Security

## Purpose
Identity, access, and DevSecOps across the fabric. Security is an input to every module's design, not a gate at the end. This capability defines the controls every module inherits by default so that "secure" is the starting state, not a later hardening step.

## Standards
- **Secure By Default:** every capability is authenticated and authorised; deny by default, least privilege.
- **DevSecOps:** security scanning runs in CI (`../delivery/`); a failing scan fails the build.
- Secrets are never in code or images; contracts and data carry classification (`../data/`).

## Governance
- **Central:** identity model, auth standards, scanning policy, secret management, classification scheme.
- **Local (FDE):** applying the controls within a module.

## Patterns
- Zero-standing-access, least-privilege per module and per agent (`../mcp/`, `../a2a/`).
- Policy-as-code enforced in the pipeline.

## Guidance
Security is designed in at step 8 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md` and enforced in CI. Applies equally to agent modules (`../agent/`).
