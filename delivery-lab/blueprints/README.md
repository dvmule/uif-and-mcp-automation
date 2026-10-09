# Playbooks

Playbooks are step-by-step golden paths for building a specific kind of capability. Where `../../capabilities/` defines the *standards* and `../../architecture-design/operating-model/fde-build-deploy-workflow.md` defines the *overall* flow, a playbook is the concrete, capability-specific walkthrough an FDE follows to build one module.

Playbooks provide guidance only — no runnable code (`../CLAUDE.md`, rule 4).

## Available now

- [`api-playbook.md`](api-playbook.md) — build a governed API module end-to-end. This is the worked golden path; the others follow its shape.

## To be written (as each capability's first module proves the pattern)

Create these when the corresponding foundation Epic delivers, not before (`../PLANNING.md`):

- `event-playbook.md` · `data-playbook.md` · `schema-playbook.md` · `semantics-playbook.md` · `knowledge-graph-playbook.md` · `mcp-playbook.md` · `a2a-playbook.md` · `agent-playbook.md` · `deployment-playbook.md`

Each new playbook mirrors the structure of `api-playbook.md`: prerequisites → design → schema-first → implement → test → CI → CD → operate.
