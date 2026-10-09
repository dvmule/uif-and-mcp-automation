# Delivery Lab

## What this is and why it matters

The starter material an FDE copies from when spinning up new work. Nothing here is a running service — it is templates, accelerators, blueprints, and starter kits that make the golden path the path of least resistance. If a domain team is writing boilerplate by hand, something that belongs here is missing.

## Contents

| Folder | What it holds |
|---|---|
| `templates/` | File- and module-level templates: the canonical shapes for a Dockerfile, a GitHub Actions workflow, a Terraform module, a service skeleton. |
| `accelerators/` | Opinionated, larger scaffolds that assemble multiple templates into a ready-to-fill starting point for a common kind of work. |
| `blueprints/` | Step-by-step golden-path guidance (playbooks) — how to go from a Jira Story to a shipped capability. Includes the API playbook. |
| `starter-kits/` | Per-capability quick starts — the minimum to stand up an API, an event producer, an MCP server, an agent. |

## Relationship to the rest of the fabric

`delivery-lab/` provides the *starting shapes*; `../capabilities/` provides the *standards* those shapes conform to; `../domains/` is where a copied shape becomes real code. The domain bootstrapper `../create-domain.sh` stamps a new domain from `../_domain-template/`, which itself is built from these materials.

## Governance

Central. Add to the lab when the third team copies the same thing by hand — not before.
