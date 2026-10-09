# product / src/api — the product domain's API contracts

This directory holds **contracts, not code**. Product's gold entities are read
through the shared gold serving copy, and a domain does not ship a service of
its own to read them ([ADR-0026](../../../../architecture-design/decisions/0026-gold-entity-read-api-is-the-shared-serving-copy.md)).

One directory per entity, per [ADR-0022](../../../../architecture-design/decisions/0022-gold-schema-pipeline-repo-first-registry-next.md)
decision 4:

| Entity | Contract |
|---|---|
| `product` | [`product/openapi.yaml`](product/openapi.yaml) |
| `marketing-copy` | [`marketing-copy/openapi.yaml`](marketing-copy/openapi.yaml) |

## What serves these

The shared retrieval function, over the shared serving table — one function and
one table for every gold entity across every domain. An entity joins by
declaring `serving.enabled` in its pipeline configuration; nothing here is
deployed and no infrastructure belongs to this directory.

The response schema is **not** written here. Each contract `$ref`s the generated
api-tier projection under `../schemas/channels/{entity}/`, which the schema
pipeline derives from that entity's gold master. A hand-authored copy of a
generated schema is a restatement, and the two drift silently — which is what
happened here: this contract restated 71 properties against a generated 60 for
weeks, flattening a nested object into twelve dotted names, and nothing caught
it. A guard now holds every served entity's contract to its master.

## Endpoints

The read surface follows the entity's business key, so it differs per entity.
For `product`, whose business key is two columns:

| Operation | Purpose |
|---|---|
| `GET /product/{productId}/organization/{organizationCode}` | One product as one organization holds it |
| `GET /product/{productId}` | Every organization carrying that product, paged |

Note the key a caller uses is **not** the key the gold table merges on. Gold
merges product on source-system surrogates that no channel publishes; the
business pair above is the read surface, resolved through the serving copy's
business-key index.

## What used to be here

A FastAPI application serving `GET /api/v1/products` and
`POST /api/v1/products/query` over Athena, with a Helm chart, an IRSA role and
an image repository. It was removed by ADR-0026. It had never been deployed: its
cloud substrate was gated off in every environment, could not produce a plan
with the gate open, and its promotion workflow never ran.

Its `POST /products/query` took a caller's column list, which the shared read
does not. ADR-0026 records that capability as removed and deliberately does not
decide where it should live if a consumer asks for it.
