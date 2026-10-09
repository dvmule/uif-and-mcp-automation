# product / infrastructure/helm

Helm chart. **This domain ships none.**

Product's gold entities are read through the shared serving copy, and a
domain does not build or operate a service of its own to read them
([ADR-0026](../../../../architecture-design/decisions/0026-gold-entity-read-api-is-the-shared-serving-copy.md)).
Its pipeline runs as the Echo stack's Glue jobs and Lambdas. Nothing here
is deployed to a cluster.

A chart for a containerised product API lived here until ADR-0026. It had
never been applied: the cloud substrate it needed was gated off in every
environment and could not produce a plan, and the promotion workflow that
would have used the chart never ran. The three other gold domains carry
this placeholder for the same reason.
