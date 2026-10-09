# program / src/schemas

Registered contracts for this domain (../../../stacks/apicurio/standards/). The
program entity's gold contract lives here in two authored files plus the
compiled runtime artifacts.

| Path | Role | Authored or compiled |
|---|---|---|
| `gold/program/v1/master.json` | **PRIMARY** — what `gold_program.program` IS: attributes, types, consumer tiers (`x-channels`), array shapes, and the key (`metadata.primaryKey`). Read by the transform at runtime. See its `README.md` for the entity contract | authored |
| `mapping/program/v1/program.mapping.json` | The intake — **rules only**: driving table, entity key, transform registry, and how each of the 12 columns is populated. Validated by `stacks/apicurio/standards/gold-attribute-mapping.schema.json` | authored |
| `mapping/program/v1/manifest.yaml` | Sources + `key_mapping`, `sot_filter`, lineage, output column order, array sort keys, Spark types | compiled — not yet written ([UIFPE-120](https://pearson.atlassian.net/browse/UIFPE-120)) |
| `mapping/program/v1/sql/program.sql` | The Silver → Gold query | compiled — not yet written ([UIFPE-120](https://pearson.atlassian.net/browse/UIFPE-120)) |
| `events/program-{created,updated}-{data,example}.json` | The `com.pearson.program.program.created.v1` / `….updated.v1` event contracts: data schemas (exact projections of master's event tier, gated by `../../qe/test_event_schemas.py`) and worked envelope examples | authored |

The division of labour: **master.json says what, the mapping says how, the SQL
and manifest are derived.** Edit the authored pair; regenerate the rest.

Both authored files are gated by `../../qe/` — run `py -m pytest domains/program/qe -q`.
The gates enforce schema validity, that every non-driving source declares its
join, that transforms are structured rather than prose, and that the two files
agree on coverage, order, types, nullability, channels and the key.
