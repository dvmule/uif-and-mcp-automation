# Dockerfile — the single build/deploy contract for the product domain.
#
# THIS DOMAIN SHIPS NO IMAGE. Its gold entity is read through the shared
# serving copy (ADR-0026), and its pipeline runs as the Echo stack's Glue
# jobs and Lambdas, so there is nothing here to containerise. The file is
# kept because domain-ci.yml's structural gate requires every domain to
# have one; `legal` and `hr` carry the same placeholder for the same
# reason, and whether that gate should be conditional is still open.
#
# The FastAPI application this used to build was removed with ADR-0026: it
# served two operations the shared copy now serves, its cloud substrate was
# gated off and could not plan, and it was never deployed.
