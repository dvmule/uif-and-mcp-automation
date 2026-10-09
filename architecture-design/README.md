# Architecture & Design

## What this is and why it matters

This is the "why and how it's shaped" layer of UIF. Before any domain team writes code, this folder tells them what the fabric values, what standards they must conform to, and which proven shapes to reach for. It is the top-level home for the thinking that governs every domain and every capability — promoted out of the capability list because it is not one discipline among many, it is the frame the others sit in.

Nothing here is executable. It is principles, standards, patterns, decisions, and reference architectures — read first, applied everywhere.

## Contents

| Folder | What it holds |
|---|---|
| `principles/` | The non-negotiables — the engineering principles every domain and capability upholds (API First, Secure By Default, Schema Registry First, Observable By Default, MCP/A2A/Agent Ready…). |
| `standards/` | The single home for cross-cutting engineering standards: naming, versioning, error handling, SLOs, review gates. Capability-specific standards live under `../capabilities/<x>/`; anything that spans capabilities lives here. |
| `patterns/` | Reusable solution patterns — the recurring shapes a domain team composes from. |
| `decisions/` | Architecture Decision Records (ADRs). One file per decision, immutable once accepted. |
| `reference-architectures/` | End-to-end blueprints showing how capabilities compose into a working domain. |
| `operating-model/` | Four-in-a-Box roles and the FDE build & deploy workflow — WHO does the work and HOW it flows. |

## Governance

- **Central:** everything in this folder is a UIF standard. It does not vary per domain.
- **Local (FDE):** a domain applies these; it does not redefine them. Propose changes here via an ADR in `decisions/`.
