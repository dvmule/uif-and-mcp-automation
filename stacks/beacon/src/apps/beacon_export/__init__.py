"""beacon — Gold-to-partner export (RELAY Beacon pattern).

Domain-agnostic export pipeline: any domain (product, assessment, hr, legal,
identity, ...) exports a Gold dataset to a partner endpoint by supplying two
configs — a dynamic-mapping engine config (the transform) and an export
config (gates, renderings, packaging, delivery). No domain-specific code
lives in this package.

Pipeline stages: transform (dynamic-mapping engine) -> quality gate ->
render (CSV/JSON/XML) -> package (naming, manifest, checksums) -> deliver
(SFTP, tmp-then-rename).

Every module except beacon_export_job.py is free of Spark imports so
tests/ can unit-test them without a Spark runtime. Design:
docs/beacon-product-export-design.md in the dynamic-mapping repo.
"""
