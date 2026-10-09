# Schemas: owned by the domains, not this stack

The event **contracts** are domain property (Schema Registry First, but at
the right owner): each domain registers its gold master (+ `x-channels`),
its per-operation event data schemas, its channel-projected data schemas
and its `asyncapi.yaml` under `domains/{domain}/src/schemas/` — for product
see `domains/product/src/schemas/events/` and
`domains/product/src/events/asyncapi.yaml`.

This stack is the domain-agnostic *carrier*: what it owns are the
**envelope invariants** (Pearson CloudEvents 1.0 profile, per-operation
DetailTypes `{domain}.{operation}.{version}`, the `channel` discriminator),
which are pinned executable-y by `qe/test_event_fabric_processor.py`'s
`ENVELOPE_SCHEMA`, and the **table contracts** in [`../../docs/`](../../docs/)
(pipeline/publish/delivery metrics, subscription-configs).

Channel data schemas are derived from a domain's gold master by the one
generator at `stacks/echo/src/apps/cli/generate_tier_subsets.py`
(UIFPE-523 retired this stack's duplicate). The output lands in that
domain's tree, never here.
