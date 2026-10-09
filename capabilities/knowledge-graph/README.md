# Knowledge Graph

## Purpose
A linked, queryable representation of enterprise knowledge — entities and their relationships across capabilities — so that agents and applications can reason over connected context rather than isolated records.

## Standards
- **Knowledge Graph Enabled:** entities and relationships use the canonical semantics (`../semantics/`).
- The graph is populated from governed data products and events, not by scraping (`../data/`, `../event/`).

## Governance
- **Central:** the graph store, the ingestion pattern, the query interface, entity resolution rules.
- **Local (FDE):** the subgraph a module contributes.

## Patterns
- Ingest from data products/events → resolve entities → expose query interface.
- Graph-backed retrieval for agents (RAG over the graph).

## Guidance
Foundation Epic: `Knowledge Graph Foundation`; store selection is a Spike (`../../PLANNING.md`). Consumed by `../agent/`.
