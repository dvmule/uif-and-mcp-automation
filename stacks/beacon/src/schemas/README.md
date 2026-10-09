# beacon — schemas

The contract this domain registers is the **export config pair**: a mapping
config (sources, mappings, filters, validations) plus an export config
(renderings, packaging, delivery, quality gates). The authoritative validator
is code shared with the runtime — `src/apps/beacon_export/export_config.py`
and the engine's `config_validator.py` — so the control panel can never accept
a config the pipeline would reject.

- `examples/customer_orders.json` — worked mapping-config example (two
  sources, a join, derived fields, validations).
- Consuming domains keep their own published config pairs under **their**
  `src/schemas/beacon/<job>/v<N>/` — see `domains/product/src/schemas/beacon/`.
  At runtime the promoted copy lives in S3 (`configs/<job>/v<N>/`, immutable —
  design decision D22).
