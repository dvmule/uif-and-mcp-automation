# templates/

Starter kits an FDE copies to create a new module. A template is the fastest correct starting point for a capability — it ships the mandatory module anatomy (`../../delivery-lab/reference-implementations/README.md`) so an FDE fills in logic rather than assembling scaffolding.

Templates provide starter kits only; they are not runnable services and are never deployed as-is (`../CLAUDE.md`, rule 4).

## How to use

1. Pick the template matching your capability.
2. Copy it to `domains/<domain>/`.
3. Follow the capability playbook (`../blueprints/`).

## Planned templates

Create each template when the corresponding capability's first module proves the pattern — reference-quality first, then generalised into a template (`../PLANNING.md`). Do not pre-create empty template folders.

- `api-template/` · `event-template/` · `agent-template/` · `mcp-template/` · `a2a-template/`
- `terraform-template/` · `github-actions-template/`

Each module template contains the mandatory anatomy: `src/`, `tests/`, `Dockerfile`, `terraform/`, `helm/`, plus capability-specific files (e.g. `openapi.yaml` for API, `prompts/` + `evals/` for Agent).
